"""Guards for a public deployment: request size, per-visitor rates, the LLM's daily budget and admin access."""
from __future__ import annotations

import secrets
from typing import Callable

from fastapi import Header, HTTPException, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import clock
from .cache import cache
from .config import settings

HOUR = 3600
DAY = 24 * HOUR


class _TooLarge(Exception):
    pass


class BodyLimit:
    """Refuse oversized request bodies, including ones that don't declare a length up front."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        too_large = JSONResponse({"detail": "That request is too large."}, status_code=413)
        declared = dict(scope["headers"]).get(b"content-length", b"0")
        if not declared.isdigit() or int(declared) > settings.max_request_bytes:
            await too_large(scope, receive, send)
            return

        seen = 0
        overflowed = False

        async def counting_receive() -> Message:
            nonlocal seen, overflowed
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > settings.max_request_bytes:
                overflowed = True
                raise _TooLarge
            return message

        async def guarded_send(message: Message) -> None:
            if not overflowed:  # FastAPI reports a failed body read as a 400; the 413 below replaces it
                await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except _TooLarge:
            pass
        if overflowed:
            await too_large(scope, receive, send)


def client_ip(request: Request) -> str:
    """Who a request counts against. A trusted proxy appends the address it saw, so count back from the end; earlier entries can be faked.

    Without a trusted proxy everyone shares one limit. request.client can't tell visitors apart here: behind the
    Next.js rewrite it's the frontend's address, and uvicorn rewrites it from the header for local connections.
    """
    hops = settings.trusted_proxy_hops
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    return forwarded[-hops] if hops > 0 and len(forwarded) >= hops else "shared"


def per_ip(name: str, limit: Callable[[], int]):
    """A route dependency allowing `limit()` requests an hour from each visitor."""

    def check(request: Request) -> None:
        if cache.incr(f"rate:{name}:{client_ip(request)}", HOUR) > limit():
            raise HTTPException(429, "Too many requests right now. Please try again a bit later.")

    return check


limit_trips = per_ip("trips", lambda: settings.trips_per_hour_per_ip)
limit_messages = per_ip("messages", lambda: settings.messages_per_hour_per_ip)
limit_searches = per_ip("searches", lambda: settings.searches_per_hour_per_ip)


def _llm_key() -> str:
    return f"llm-tokens:{clock.today().isoformat()}"


def llm_budget_left() -> bool:
    return int(cache.get(_llm_key()) or 0) < settings.llm_tokens_per_day


def record_llm_tokens(tokens: int) -> None:
    cache.incr(_llm_key(), DAY, amount=tokens)


def require_admin(authorization: str | None = Header(default=None)) -> None:
    """With no ADMIN_TOKEN set, nobody gets in: forgetting it must not leave results public."""
    expected = settings.admin_token
    supplied = (authorization or "").removeprefix("Bearer ")
    if not expected or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Admin token required.", headers={"WWW-Authenticate": "Bearer"})


def known_tester(code: str | None) -> str | None:
    """Only codes we handed out count toward results. Anyone else can still use the app; their trip just goes untagged."""
    return code if code in settings.tester_codes else None
