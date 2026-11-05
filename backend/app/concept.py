"""Concept test results: which plan each tester would book, and what they said about it."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Event, PlanSearch

ANSWERS = ("yes", "maybe", "no")


def _per_trip(session: Session, name: str) -> dict[str, list[Event]]:
    """Every event of this kind for each trip, oldest first."""
    out: dict[str, list[Event]] = {}
    for row in session.scalars(select(Event).where(Event.name == name).order_by(Event.id)):
        out.setdefault(row.trip_id, []).append(row)
    return out


def _same_plan(a: Event, b: Event) -> bool:
    return a.props["search_id"] == b.props["search_id"] and a.props["plan"] == b.props["plan"]


def trip_rows(session: Session) -> list[dict]:
    """One row per trip with a choice or an answer, oldest activity first.

    A tester can change their mind, so a row is about the plan they acted on last, and it only
    carries an answer they gave about that plan. An answer about another plan stays in the events.
    """
    created = {e.trip_id: e.props for e in session.scalars(select(Event).where(Event.name == "trip_created"))}
    all_choices = _per_trip(session, "plan_chosen")
    all_answers = _per_trip(session, "concept_answer")
    latest = {t: max(all_choices.get(t, []) + all_answers.get(t, []), key=lambda e: e.id) for t in all_choices.keys() | all_answers.keys()}
    rows = []
    for trip_id in sorted(latest, key=lambda t: latest[t].id):
        props = latest[trip_id].props
        choice = next((e for e in reversed(all_choices.get(trip_id, [])) if _same_plan(e, latest[trip_id])), None)
        answer = next((e for e in reversed(all_answers.get(trip_id, [])) if _same_plan(e, latest[trip_id])), None)
        search = session.get(PlanSearch, props["search_id"])
        plan = next((p for p in search.result["plans"] if p["id"] == props["plan"]), None) if search else None
        origin = created.get(trip_id, {})
        rows.append({
            "tester": origin.get("tester") or "",
            "trip_id": trip_id,
            "trip": origin.get("demo") or "typed",
            "search_id": props["search_id"],
            "plan": props["plan"] or "none",
            "plan_title": plan["title"] if plan else "",
            "plan_total": plan["totals"]["likely"] if plan else "",
            "recommended": bool(plan and plan["recommended"]),
            "cheapest_flights": bool(plan and "Lowest booking price" in plan["tags"]),
            "would_change": answer.props["would_change"] if answer else "",
            "note": (answer.props.get("note") or "") if answer else "",
            "chosen_at": choice.created_at.isoformat(timespec="seconds") if choice else "",
            "answered_at": answer.created_at.isoformat(timespec="seconds") if answer else "",
        })
    return rows


def _counts(rows: list[dict]) -> dict:
    return {
        "chose_recommended": sum(r["recommended"] for r in rows),
        "chose_cheapest_flights": sum(r["cheapest_flights"] for r in rows),
        "chose_another_plan": sum(r["plan"] != "none" and not r["recommended"] and not r["cheapest_flights"] for r in rows),
        "chose_none": sum(r["plan"] == "none" for r in rows),
        "would_change": {option: sum(r["would_change"] == option for r in rows) for option in ANSWERS},
    }


def summarize(rows: list[dict]) -> dict:
    """Counts only. Notes can hold anything a tester typed, so they stay out of the metrics endpoint.

    Trips without a tester code (like your own runs) are left out. The main counts use each
    tester's most recent trip, so someone who tries three trips counts once; `by_trip` shows every trip.
    """
    tagged = [r for r in rows if r["tester"]]
    per_tester = list({r["tester"]: r for r in tagged}.values())
    return {
        "testers": len(per_tester),
        **_counts(per_tester),
        "by_trip": {kind: _counts([r for r in tagged if r["trip"] == kind]) for kind in sorted({r["trip"] for r in tagged})},
        "untagged_trips_left_out": len(rows) - len(tagged),
    }
