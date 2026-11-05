"""Tools the assistant can call. All facts and prices come from here, never from the model."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import brief as B
from ..db import Trip
from ..trips import (
    RateLimited,
    UnsearchableDates,
    current_brief,
    latest_search,
    run_search,
    update_brief,
)

BRIEF_FIELDS_SCHEMA = {
    "type": "object",
    "properties": {
        "origin": {"type": "string", "description": "Where the traveler leaves from: a New York City area neighborhood or landmark, e.g. 'Upper West Side'."},
        "destination": {"type": "string", "description": "Where in Chicago they are going: neighborhood or landmark, e.g. 'Millennium Park'."},
        "depart_date": {"type": "string", "description": "YYYY-MM-DD"},
        "return_date": {"type": "string", "description": "YYYY-MM-DD"},
        "carry_on": {"type": "integer", "enum": [0, 1], "description": "1 if bringing a full-size carry-on bag."},
        "checked_bags": {"type": "integer", "minimum": 0, "maximum": 2},
        "budget": {"type": "number", "description": "Total USD for flights, bags and all rides."},
        "priority": {"type": "string", "enum": ["cheapest", "balanced", "easiest"]},
        "arrive_by": {"type": "string", "description": "Latest landing time in Chicago, HH:MM 24-hour."},
        "will_drive": {"type": "boolean", "description": "True if they will drive their own car to the airport and park."},
    },
    "additionalProperties": False,
}

TOOLS = [
    {
        "type": "function",
        "name": "update_brief",
        "description": "Record trip details. Put details the traveler stated in `stated`. Put anything you inferred in `assumed`; it will be shown as an assumption.",
        "parameters": {
            "type": "object",
            "properties": {"stated": BRIEF_FIELDS_SCHEMA, "assumed": BRIEF_FIELDS_SCHEMA},
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_trip_state",
        "description": "Read the current trip brief, which required fields are missing, and whether plans have been searched.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "search_plans",
        "description": "Search and price complete door-to-door plans for the current brief. Only call after the traveler has asked to see or compare plans and the brief has no missing required fields.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "get_plans",
        "description": "Read the most recently priced plans, with every line item, so you can explain or compare them.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def plans_summary(result: dict) -> dict:
    return {
        "checked_at": result["checked_at"],
        "data_mode": result["data_mode"],
        "comparison": result["comparison"],
        "notes": result["notes"],
        "plans": [
            {
                "id": p["id"],
                "tags": p["tags"],
                "recommended": p["recommended"],
                "title": p["title"],
                "totals": p["totals"],
                "budget": p["budget"],
                "convenience": p["convenience"],
                "door_to_door": p["door_to_door"],
                "warnings": p["warnings"],
                "items": [{k: it[k] for k in ("label", "detail", "low", "likely", "high", "kind")} for it in p["items"]],
            }
            for p in result["plans"]
        ],
    }


class ToolBox:
    def __init__(self, session: Session, trip: Trip) -> None:
        self.session = session
        self.trip = trip
        self.brief_changed = False
        self.plans_searched = False

    def execute(self, name: str, args: dict) -> dict:
        handler = getattr(self, f"tool_{name}", None)
        if not handler:
            return {"error": f"Unknown tool {name}"}
        return handler(**args)

    def state(self) -> dict:
        brief = current_brief(self.session, self.trip)
        search = latest_search(self.session, self.trip)
        return {
            "brief": B.compact(brief),
            "missing_required": B.missing_required(brief),
            "ready_to_search": B.is_ready(brief),
            "plans_searched": bool(search),
            "plans_are_current": bool(search and search.brief_version == self.trip.brief_version),
        }

    def tool_update_brief(self, stated: dict | None = None, assumed: dict | None = None) -> dict:
        errors: list[str] = []
        before = self.trip.brief_version
        if stated:
            _, errs = update_brief(self.session, self.trip, stated, "user_said", "assistant")
            errors += errs
        if assumed:
            _, errs = update_brief(self.session, self.trip, assumed, "assumed", "assistant")
            errors += errs
        self.brief_changed |= self.trip.brief_version != before
        return {**self.state(), "errors": errors}

    def tool_get_trip_state(self) -> dict:
        return self.state()

    def tool_search_plans(self) -> dict:
        brief = current_brief(self.session, self.trip)
        if not B.is_ready(brief):
            return {"error": "Brief is missing required fields", "missing_required": B.missing_required(brief)}
        try:
            row = run_search(self.session, self.trip)
        except (RateLimited, UnsearchableDates) as exc:
            return {"error": str(exc)}
        self.plans_searched = True
        return plans_summary(row.result)

    def tool_get_plans(self) -> dict:
        search = latest_search(self.session, self.trip)
        if not search:
            return {"error": "No plans have been searched yet."}
        summary = plans_summary(search.result)
        summary["plans_are_current"] = search.brief_version == self.trip.brief_version
        return summary
