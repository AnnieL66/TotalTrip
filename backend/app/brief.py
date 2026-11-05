"""The trip brief: what we know about the trip, and where each fact came from.

Every field keeps a provenance so the UI can show "You said" vs "I assumed" and
the AI never silently invents details that change the plan.
"""
from __future__ import annotations

import copy
import re
from datetime import date, timedelta
from typing import Any, Callable, Literal

from . import clock, geo

Provenance = Literal["user_said", "assumed", "missing"]

FIELD_ORDER = [
    "origin", "destination", "depart_date", "return_date", "travelers",
    "carry_on", "checked_bags", "budget", "priority", "arrive_by", "will_drive",
]
REQUIRED = ["origin", "destination", "depart_date", "return_date"]
LABELS = {
    "origin": "From",
    "destination": "To",
    "depart_date": "Leaving",
    "return_date": "Coming back",
    "travelers": "Travelers",
    "carry_on": "Carry-on",
    "checked_bags": "Checked bags",
    "budget": "Budget",
    "priority": "Priority",
    "arrive_by": "Land in Chicago by",
    "will_drive": "Getting to the airport",
}
PRIORITY_LABELS = {
    "cheapest": "Lowest total cost",
    "balanced": "Balance cost and comfort",
    "easiest": "Easiest trip, then lowest cost",
}
# Values filled in when the traveler hasn't said otherwise. Shown as "I assumed".
DEFAULTS: dict[str, Any] = {
    "travelers": 1,
    "carry_on": 1,
    "checked_bags": 0,
    "priority": "balanced",
    "will_drive": False,
}
MAX_DAYS_AHEAD = 330


class BriefError(ValueError):
    pass


def empty_brief() -> dict:
    return {name: {"value": None, "display": None, "provenance": "missing"} for name in FIELD_ORDER}


def _fmt_date(d: date) -> str:
    return d.strftime("%a, %b ") + str(d.day) + d.strftime(", %Y")


def _fmt_time(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"


def _parse_place(value: Any, metro: str, role: str) -> tuple[str, str]:
    place = geo.get_place(str(value)) or geo.resolve_place(str(value))
    if not place:
        raise BriefError(f"I couldn't find “{value}”. Try a neighborhood or landmark.")
    if place.metro != metro:
        city = "New York City area" if metro == "NYC" else "Chicago"
        raise BriefError(f"For now TotalTrip plans trips from the New York City area to Chicago, so the {role} needs to be in {city}.")
    return place.key, place.name


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise BriefError(f"“{value}” isn't a date I can read.") from exc


def _v_origin(v: Any) -> tuple[Any, str]:
    return _parse_place(v, "NYC", "starting point")


def _v_destination(v: Any) -> tuple[Any, str]:
    return _parse_place(v, "CHI", "destination")


def _v_date(v: Any) -> tuple[Any, str]:
    d = _parse_date(v)
    today = clock.today()
    if d < today:
        raise BriefError(f"{_fmt_date(d)} is in the past.")
    if d > today + timedelta(days=MAX_DAYS_AHEAD):
        raise BriefError("Fares are only published about 11 months ahead. Pick a closer date.")
    return d.isoformat(), _fmt_date(d)


def _v_travelers(v: Any) -> tuple[Any, str]:
    n = int(v)
    if n != 1:
        raise BriefError("TotalTrip's first release plans trips for one traveler.")
    return 1, "1 adult (solo trip)"


def _v_carry_on(v: Any) -> tuple[Any, str]:
    n = 1 if _truthy_count(v) else 0
    return n, "1 carry-on bag" if n else "Personal item only"


def _v_checked(v: Any) -> tuple[Any, str]:
    n = int(v)
    if not 0 <= n <= 2:
        raise BriefError("TotalTrip prices up to 2 checked bags.")
    return n, {0: "None", 1: "1 checked bag", 2: "2 checked bags"}[n]


def _v_budget(v: Any) -> tuple[Any, str]:
    if v in (None, "", 0):
        return None, "No set budget"
    amount = float(re.sub(r"[^\d.-]", "", str(v)) or 0)
    if amount < 50:
        raise BriefError("That budget looks too low to cover flights. What's the total you want to stay under?")
    return round(amount), f"${round(amount):,} for flights, bags and all rides"


def _v_priority(v: Any) -> tuple[Any, str]:
    key = str(v).lower()
    if key not in PRIORITY_LABELS:
        raise BriefError("Priority must be cheapest, balanced or easiest.")
    return key, PRIORITY_LABELS[key]


def _v_arrive_by(v: Any) -> tuple[Any, str]:
    if v in (None, ""):
        return None, "Any time"
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(v).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise BriefError(f"“{v}” isn't a time I can read. Use HH:MM, like 18:00.")
    hhmm = f"{int(m.group(1)):02d}:{m.group(2)}"
    return hhmm, f"Before {_fmt_time(hhmm)}"


def _v_will_drive(v: Any) -> tuple[Any, str]:
    drive = _truthy_count(v) > 0
    return drive, "Drive and park" if drive else "Ride or transit"


def _truthy_count(v: Any) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    return 1 if str(v).strip().lower() in {"1", "true", "yes", "y"} else 0


VALIDATORS: dict[str, Callable[[Any], tuple[Any, str]]] = {
    "origin": _v_origin,
    "destination": _v_destination,
    "depart_date": _v_date,
    "return_date": _v_date,
    "travelers": _v_travelers,
    "carry_on": _v_carry_on,
    "checked_bags": _v_checked,
    "budget": _v_budget,
    "priority": _v_priority,
    "arrive_by": _v_arrive_by,
    "will_drive": _v_will_drive,
}


def _return_after_new_departure(brief: dict, updates: dict[str, Any], depart: str) -> dict | None:
    """Moving only the departure past the return moves the whole trip, keeping its length. Shown as an assumption."""
    old_dep, old_ret = brief["depart_date"]["value"], brief["return_date"]["value"]
    if "depart_date" not in updates or "return_date" in updates or not (old_dep and old_ret) or old_ret <= old_dep:
        return None
    nights = (date.fromisoformat(old_ret) - date.fromisoformat(old_dep)).days
    try:
        value, display = _v_date(date.fromisoformat(depart) + timedelta(days=nights))
    except BriefError:
        return None
    return {
        "field": {"value": value, "display": display, "provenance": "assumed"},
        "notice": f"I moved your return to {display} to keep your {nights}-night trip. Change it if that's wrong.",
    }


def apply_updates(brief: dict, updates: dict[str, Any], provenance: Provenance) -> tuple[dict, list[str]]:
    """Validate and merge updates. Returns the new brief and human-readable errors for rejected fields."""
    new = copy.deepcopy(brief)
    errors: list[str] = []
    for name, raw in updates.items():
        if name not in VALIDATORS:
            continue
        # An assumption never overwrites something the traveler actually told us.
        if provenance == "assumed" and new[name]["provenance"] == "user_said":
            continue
        try:
            value, display = VALIDATORS[name](raw)
        except (BriefError, ValueError, TypeError, OverflowError) as exc:
            errors.append(str(exc) if isinstance(exc, BriefError) else f"Couldn't read {LABELS[name].lower()}: {raw}")
            continue
        new[name] = {"value": value, "display": display, "provenance": provenance}

    dep, ret = new["depart_date"]["value"], new["return_date"]["value"]
    if dep and ret and ret <= dep:
        moved = _return_after_new_departure(brief, updates, dep)
        if moved:
            new["return_date"] = moved["field"]
            errors.append(moved["notice"])
            return new, errors
        errors.append("The return date needs to be after the departure date.")
        for name in ("depart_date", "return_date"):
            if name in updates:
                new[name] = copy.deepcopy(brief[name])
    return new, errors


def with_defaults(brief: dict) -> dict:
    """Fill unset optional fields with labeled assumptions."""
    missing_defaults = {k: v for k, v in DEFAULTS.items() if brief[k]["provenance"] == "missing"}
    filled, _ = apply_updates(brief, missing_defaults, "assumed")
    for name in ("budget", "arrive_by"):
        if filled[name]["provenance"] == "missing":
            filled[name]["display"] = "No set budget" if name == "budget" else "Any time"
    return filled


def missing_required(brief: dict) -> list[str]:
    return [name for name in REQUIRED if brief[name]["value"] in (None, "")]


def is_ready(brief: dict) -> bool:
    return not missing_required(brief)


def date_problem(brief: dict) -> str | None:
    """Why this brief's dates can't be searched right now. Dates can go stale after they were validated."""
    dep, ret = brief["depart_date"]["value"], brief["return_date"]["value"]
    if not (dep and ret):
        return None
    if ret <= dep:
        return "The return date needs to be after the departure date. Fix your dates to compare plans."
    if date.fromisoformat(dep) < clock.today():
        return f"{brief['depart_date']['display']} is in the past. Pick new dates to compare plans."
    return None


def assumptions(brief: dict) -> list[str]:
    return [name for name in FIELD_ORDER if brief[name]["provenance"] == "assumed"]


def nights(brief: dict) -> int | None:
    dep, ret = brief["depart_date"]["value"], brief["return_date"]["value"]
    if not (dep and ret):
        return None
    return (date.fromisoformat(ret) - date.fromisoformat(dep)).days


def public_view(brief: dict) -> dict:
    """Shape sent to the frontend."""
    return {
        "fields": [
            {"name": name, "label": LABELS[name], **brief[name], "required": name in REQUIRED}
            for name in FIELD_ORDER
        ],
        "missing": missing_required(brief),
        "assumed": assumptions(brief),
        "ready": is_ready(brief),
        "nights": nights(brief),
    }


def compact(brief: dict) -> dict:
    """Shape sent to the AI: values plus provenance, nothing else."""
    return {name: {"value": brief[name]["value"], "display": brief[name]["display"], "provenance": brief[name]["provenance"]} for name in FIELD_ORDER}
