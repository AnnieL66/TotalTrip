"""TotalTrip API."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import brief as B
from . import geo
from .ai.agent import respond
from .ai.tools import ToolBox
from .config import settings
from .db import Event, Message, PlanSearch, PriceReport, SpendReport, Trip, get_session, init_db, log_event
from .trips import RateLimited, UnsearchableDates, create_demo_trip, create_trip, current_brief, demo_trips, history, latest_search, run_search, update_brief

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="TotalTrip API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def _invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    """The default response echoes the bad input, which fails to serialize if that input is Infinity."""
    return JSONResponse(status_code=422, content={"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]})


# ---------------------------------------------------------------- helpers


def _trip(session: Session, trip_id: str) -> Trip:
    trip = session.get(Trip, trip_id)
    if not trip:
        raise HTTPException(404, "Trip not found")
    return trip


def _search_view(row: PlanSearch | None, trip: Trip) -> dict | None:
    if not row:
        return None
    return {**row.result, "brief_version": row.brief_version, "is_current": row.brief_version == trip.brief_version}


def _plan_in_search(search: PlanSearch | None, plan_id: str | None) -> dict | None:
    if not search:
        return None
    return next((p for p in search.result["plans"] if p["id"] == plan_id), None)


def _followed_plan(session: Session, trip: Trip) -> dict | None:
    """The plan the traveler last clicked through to book. It may be from an older search than the latest one."""
    click = session.scalar(
        select(Event).where(Event.trip_id == trip.id, Event.name == "click_to_book").order_by(Event.id.desc()).limit(1)
    )
    search_id = click.props.get("search_id") if click else None
    search = session.get(PlanSearch, search_id) if search_id else None
    plan = _plan_in_search(search, click.props.get("plan")) if click else None
    if not plan:
        return None
    return {"search_id": search.id, "plan_id": plan["id"], "title": plan["title"], "likely": plan["totals"]["likely"]}


def _state(session: Session, trip: Trip) -> dict:
    return {
        "id": trip.id,
        "brief": B.public_view(current_brief(session, trip)),
        "brief_version": trip.brief_version,
        "messages": [{"id": m.id, "role": m.role, "content": m.content} for m in history(session, trip, limit=200)],
        "plans": _search_view(latest_search(session, trip), trip),
        "followed_plan": _followed_plan(session, trip),
        "assistant_mode": settings.assistant_mode,
    }


GREETING = (
    "Hi! I'm your TotalTrip planner. Tell me where you're leaving from, where in Chicago you're going, "
    "and your dates. I'll add up flights, bags and every ride, door to door."
)


def _chat(session: Session, trip: Trip, text: str) -> dict:
    past = history(session, trip)
    session.add(Message(trip_id=trip.id, role="user", content=text))
    session.flush()
    out = respond(ToolBox(session, trip), past, text)
    session.add(Message(trip_id=trip.id, role="assistant", content=out["reply"]))
    log_event(session, "message", trip.id, assistant_mode=out["assistant_mode"], brief_changed=out["brief_changed"])
    session.commit()
    return {**_state(session, trip), "reply": out["reply"], "plans_searched": out["plans_searched"]}


# ---------------------------------------------------------------- routes


class NewTrip(BaseModel):
    message: str | None = Field(default=None, max_length=2000)


class ChatIn(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class EventIn(BaseModel):
    """Only what the browser reports itself. Clicks, searches and LLM usage are logged by the server."""

    trip_id: str
    name: Literal["plans_viewed", "plan_explored"]
    props: dict[str, Any] = Field(default_factory=dict)


Dollars = Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)]


class PriceReportIn(BaseModel):
    plan_id: str | None = Field(default=None, max_length=8)
    item_label: str | None = Field(default=None, max_length=200)
    shown_price: Dollars | None = None
    observed_price: Dollars | None = None
    note: str | None = Field(default=None, max_length=2000)


class SpendIn(BaseModel):
    plan_id: str | None = Field(default=None, max_length=8)
    search_id: str | None = Field(default=None, max_length=32)
    actual: dict[Literal["flights", "bags", "rides", "other"], Dollars]
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _something_was_spent(self) -> "SpendIn":
        if sum(self.actual.values()) <= 0:
            raise ValueError("Enter at least one amount above zero.")
        return self


@app.get("/api/meta")
def meta() -> dict:
    return {
        "assistant_mode": settings.assistant_mode,
        "flight_data": settings.flight_data_mode,
        "routing": "google" if settings.google_maps_api_key else "estimate",
        "launch_market": {"origin": "New York City area", "destination": "Chicago", "travelers": 1},
    }


@app.get("/api/places")
def places() -> dict:
    """Neighborhoods and landmarks the launch market knows, for brief-editing suggestions."""
    out: dict[str, list[str]] = {"NYC": [], "CHI": []}
    for place in geo.all_places():
        if not place.generic:
            out[place.metro].append(place.name)
    return out


@app.post("/api/trips")
def new_trip(body: NewTrip, session: Session = Depends(get_session)) -> dict:
    trip = create_trip(session)
    session.add(Message(trip_id=trip.id, role="assistant", content=GREETING))
    log_event(session, "trip_created", trip.id, with_message=bool(body.message))
    if body.message and body.message.strip():
        return _chat(session, trip, body.message.strip())  # commits the trip with its first turn
    session.commit()
    return _state(session, trip)


@app.post("/api/trips/demo/{key}")
def new_demo_trip(key: str, session: Session = Depends(get_session)) -> dict:
    demo = demo_trips().get(key)
    if not demo:
        raise HTTPException(404, "Unknown demo trip")
    trip = create_demo_trip(session, demo)
    log_event(session, "trip_created", trip.id, demo=key)
    session.commit()
    return _state(session, trip)


@app.get("/api/trips/{trip_id}")
def get_trip(trip_id: str, session: Session = Depends(get_session)) -> dict:
    return _state(session, _trip(session, trip_id))


@app.post("/api/trips/{trip_id}/messages")
def post_message(trip_id: str, body: ChatIn, session: Session = Depends(get_session)) -> dict:
    return _chat(session, _trip(session, trip_id), body.text)


@app.patch("/api/trips/{trip_id}/brief")
def edit_brief(trip_id: str, updates: dict[str, Any], session: Session = Depends(get_session)) -> dict:
    """Direct edits from the brief panel always count as 'You said'."""
    trip = _trip(session, trip_id)
    _, errors = update_brief(session, trip, updates, "user_said", "user")
    log_event(session, "brief_edited", trip.id, fields=list(updates))
    session.commit()
    return {**_state(session, trip), "errors": errors}


@app.post("/api/trips/{trip_id}/plans")
def search(trip_id: str, recheck: bool = False, session: Session = Depends(get_session)) -> dict:
    trip = _trip(session, trip_id)
    brief = current_brief(session, trip)
    if not B.is_ready(brief):
        raise HTTPException(422, {"message": "Your brief needs a few more details.", "missing": B.missing_required(brief)})
    try:
        row = run_search(session, trip, refresh=recheck)
    except UnsearchableDates as exc:
        raise HTTPException(422, {"message": str(exc), "missing": []}) from exc
    except RateLimited as exc:
        raise HTTPException(429, str(exc)) from exc
    session.commit()
    return _search_view(row, trip)


@app.get("/api/trips/{trip_id}/plans")
def get_plans(trip_id: str, session: Session = Depends(get_session)) -> dict:
    trip = _trip(session, trip_id)
    view = _search_view(latest_search(session, trip), trip)
    if not view:
        raise HTTPException(404, "No plans yet")
    return view


def _allowed_outbound(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:  # e.g. a malformed IPv6 host
        return False
    sites = {urlparse(a["site"]).hostname for a in geo.airport_data()["airlines"].values()}
    return parsed.scheme == "https" and host in sites


@app.get("/api/out")
def outbound(
    url: str,
    trip: str | None = None,
    plan: str | None = None,
    search_id: str | None = Query(default=None, alias="search"),
    session: Session = Depends(get_session),
):
    """Tracked click-through to a provider site. Only known airline sites are allowed (no open redirect).

    The traveler always gets redirected, but only a click on a link we actually showed them is counted.
    """
    if not _allowed_outbound(url):
        raise HTTPException(400, "Unknown destination")
    search = session.get(PlanSearch, search_id) if search_id else None
    shown = _plan_in_search(search, plan) if search and search.trip_id == trip else None
    if shown and any(link["url"] == url for link in shown["booking_links"]):
        log_event(session, "click_to_book", trip, plan=plan, search_id=search_id, url=url)
        session.commit()
    return RedirectResponse(url, status_code=302)


@app.post("/api/events")
def post_event(body: EventIn, session: Session = Depends(get_session)) -> dict:
    trip = _trip(session, body.trip_id)
    session.add(Event(trip_id=trip.id, name=body.name, props=body.props))
    session.commit()
    return {"ok": True}


@app.post("/api/trips/{trip_id}/price-reports")
def report_price(trip_id: str, body: PriceReportIn, session: Session = Depends(get_session)) -> dict:
    trip = _trip(session, trip_id)
    search = latest_search(session, trip)
    session.add(PriceReport(trip_id=trip.id, search_id=search.id if search else None, **body.model_dump()))
    log_event(session, "price_reported", trip.id, plan=body.plan_id, item=body.item_label)
    session.commit()
    return {"ok": True}


@app.post("/api/trips/{trip_id}/spend")
def report_spend(trip_id: str, body: SpendIn, session: Session = Depends(get_session)) -> dict:
    trip = _trip(session, trip_id)
    estimated = None
    if body.plan_id:
        # "Plan A" differs between searches, so the report has to say which search it means.
        search = session.get(PlanSearch, body.search_id) if body.search_id else None
        plan = _plan_in_search(search, body.plan_id) if search and search.trip_id == trip.id else None
        if not plan:
            raise HTTPException(422, "That plan isn't part of this trip's searches.")
        estimated = plan["totals"]["likely"]
    search_id = body.search_id if body.plan_id else None
    session.add(SpendReport(trip_id=trip.id, plan_id=body.plan_id, search_id=search_id, estimated_total=estimated, actual=body.actual, note=body.note))
    log_event(session, "spend_reported", trip.id, plan=body.plan_id)
    session.commit()
    actual_total = sum(body.actual.values())
    return {"ok": True, "estimated_total": estimated, "actual_total": actual_total,
            "difference": None if estimated is None else round(actual_total - estimated, 2)}


@app.get("/api/admin/metrics")
def metrics(session: Session = Depends(get_session)) -> dict:
    """Pilot dashboard numbers: the deck's success measures."""
    counts = dict(session.execute(select(Event.name, func.count()).group_by(Event.name)).all())
    trips_with_plans = session.scalar(select(func.count(func.distinct(PlanSearch.trip_id)))) or 0
    trips_clicked = session.scalar(
        select(func.count(func.distinct(Event.trip_id))).where(
            Event.name == "click_to_book", Event.trip_id.in_(select(PlanSearch.trip_id))
        )
    ) or 0
    usage = session.scalars(select(Event.props).where(Event.name == "llm_usage")).all()
    spend = session.scalars(select(SpendReport)).all()
    # Reports with no search_id were compared with whichever search was latest, so their estimates can't be trusted.
    diffs = [sum(s.actual.values()) - s.estimated_total for s in spend if s.estimated_total and s.search_id]
    return {
        "trips": session.scalar(select(func.count()).select_from(Trip)),
        "trips_with_plans": trips_with_plans,
        "trips_clicked_to_book": trips_clicked,
        "click_to_book_rate": round(trips_clicked / trips_with_plans, 3) if trips_with_plans else None,
        "events": counts,
        "llm_tokens": {
            "input": sum(u.get("input_tokens", 0) for u in usage),
            "output": sum(u.get("output_tokens", 0) for u in usage),
        },
        "plan_searches": session.scalar(select(func.count()).select_from(PlanSearch)),
        "price_reports": session.scalar(select(func.count()).select_from(PriceReport)),
        "spend_reports": len(spend),
        "avg_actual_minus_estimate": round(sum(diffs) / len(diffs), 2) if diffs else None,
    }
