"""Trip state operations shared by the API and the assistants."""
from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import brief as B
from . import clock
from .cache import cache
from .config import DATA_DIR, settings
from .db import BriefVersion, Message, PlanSearch, Trip, log_event
from .engine.plans import search_plans


def create_trip(session: Session) -> Trip:
    trip = Trip()
    session.add(trip)
    session.flush()
    save_brief(session, trip, B.with_defaults(B.empty_brief()), "assistant")
    return trip


@lru_cache
def demo_trips() -> dict:
    return json.loads((DATA_DIR / "demo_trips.json").read_text())["trips"]


def demo_dates() -> tuple[date, date]:
    """Thursday to Monday, at least three weeks out. The same days and lead time keep demo prices the same on any date."""
    earliest = clock.today() + timedelta(days=21)
    depart = earliest + timedelta(days=(3 - earliest.weekday()) % 7)
    return depart, depart + timedelta(days=4)


def create_demo_trip(session: Session, demo: dict) -> Trip:
    """A trip with a finished brief, so a demo can start with one click. It doesn't go through the assistant, so it behaves the same in every mode."""
    trip = create_trip(session)
    depart, ret = demo_dates()
    brief, errors = update_brief(session, trip, {**demo["brief"], "depart_date": depart.isoformat(), "return_date": ret.isoformat()}, "user_said", "user")
    if errors:
        raise ValueError(f"Bad demo trip: {errors}")
    reply = (
        f"Got it. I filled in your trip brief for {brief['depart_date']['display']} to {brief['return_date']['display']}. "
        "Change anything you like, then press Compare complete plans."
    )
    session.add(Message(trip_id=trip.id, role="user", content=demo["message"]))
    session.add(Message(trip_id=trip.id, role="assistant", content=reply))
    return trip


def current_brief(session: Session, trip: Trip) -> dict:
    row = session.scalar(
        select(BriefVersion).where(BriefVersion.trip_id == trip.id, BriefVersion.version == trip.brief_version)
    )
    return row.data if row else B.with_defaults(B.empty_brief())


def save_brief(session: Session, trip: Trip, data: dict, changed_by: str) -> None:
    trip.brief_version += 1
    session.add(BriefVersion(trip_id=trip.id, version=trip.brief_version, data=data, changed_by=changed_by))


def update_brief(session: Session, trip: Trip, updates: dict, provenance: B.Provenance, changed_by: str) -> tuple[dict, list[str]]:
    old = current_brief(session, trip)
    new, errors = B.apply_updates(old, updates, provenance)
    new = B.with_defaults(new)
    if new != old:
        save_brief(session, trip, new, changed_by)
    return new, errors


def history(session: Session, trip: Trip, limit: int = 40) -> list[Message]:
    rows = session.scalars(
        select(Message).where(Message.trip_id == trip.id).order_by(Message.id.desc()).limit(limit)
    ).all()
    return list(reversed(rows))


def latest_search(session: Session, trip: Trip) -> PlanSearch | None:
    return session.scalar(
        select(PlanSearch).where(PlanSearch.trip_id == trip.id).order_by(PlanSearch.created_at.desc()).limit(1)
    )


class RateLimited(RuntimeError):
    pass


class UnsearchableDates(ValueError):
    pass


def run_search(session: Session, trip: Trip, refresh: bool = False) -> PlanSearch:
    """Search plans for the current brief, rate-limited per trip, and store the result."""
    brief = current_brief(session, trip)
    problem = B.date_problem(brief)
    if problem:
        raise UnsearchableDates(problem)
    if cache.incr(f"search-rate:{trip.id}", 3600) > settings.searches_per_hour:
        raise RateLimited("Too many searches for this trip in the last hour. Try again a bit later.")
    result = search_plans(brief, refresh=refresh)
    row = PlanSearch(id=result["search_id"], trip_id=trip.id, brief_version=trip.brief_version, result=result)
    session.add(row)
    log_event(
        session, "plans_searched", trip.id,
        search_id=row.id, refresh=refresh, data_mode=result["data_mode"],
        candidates=result["candidates_considered"], plans=len(result["plans"]),
    )
    return row
