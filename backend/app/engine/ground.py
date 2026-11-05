"""Ground-transport options for a single leg (home ↔ airport, airport ↔ destination)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from .. import geo
from ..adapters.routing import is_peak
from .rates import rates


@dataclass
class GroundOption:
    mode: str  # "rideshare" | "transit" | "drive"
    label: str
    minutes: int  # likely door-to-door minutes for this leg
    minutes_low: int
    minutes_high: int
    low: float
    likely: float
    high: float
    kind: str  # "estimate" | "quote"
    source: str
    notes: list[str] = field(default_factory=list)
    transfers: int = 0


def _r(x: float) -> float:
    return float(round(x))


def _toll(card: dict, place: geo.Place, airport_code: str, to_airport: bool) -> float:
    """The Hudson toll, charged only when a trip crosses the river into New York. Leaving New York is free."""
    if "hudson_toll" not in card:
        return 0
    place_in_nj = "nj" in place.flags
    airport_in_nj = airport_code in card["nj_airports"]
    entering_ny = place_in_nj if to_airport else airport_in_nj
    return card["hudson_toll"] if place_in_nj != airport_in_nj and entering_ny else 0


def rideshare(place: geo.Place, airport_code: str, when: datetime, routing, to_airport: bool = True) -> GroundOption:
    ap = geo.airport(airport_code)
    metro = ap["metro"]
    card = rates()["rideshare"][metro]
    drive = routing.drive(place.point, (ap["lat"], ap["lng"]), metro, when)
    fare = card["base"] + card["per_mile"] * drive.miles + card["per_minute"] * drive.minutes + card["booking_fee"]
    fare = max(fare, card["min_fare"])
    toll = _toll(card, place, airport_code, to_airport)
    fees = card["airport_fee"].get(airport_code, 0) + toll
    fees += sum(card["flag_surcharges"].get(f, 0) for f in place.flags)
    likely = fare + fees
    peak = is_peak(when)
    high_factor = card["peak_high_factor"] if peak else card["high_factor"]
    notes = []
    if toll:
        notes.append(f"Includes about ${toll:.0f} in tolls")
    if peak:
        notes.append("Rush hour: prices and traffic run higher")
    return GroundOption(
        mode="rideshare",
        label="Rideshare",
        minutes=round(drive.minutes),
        minutes_low=round(drive.minutes * 0.85),
        minutes_high=round(drive.minutes * 1.35),
        low=_r(fare * card["low_factor"] + fees),
        likely=_r(likely),
        high=_r(fare * high_factor + fees),
        kind="estimate",
        source=f"{card['source']} ({drive.source})",
        notes=notes,
    )


def _in_service(start: datetime, service: list[str]) -> bool:
    open_t, close_t = (time.fromisoformat(s) for s in service)
    return open_t <= start.time() <= close_t


def transit(place: geo.Place, airport_code: str, checked_bags: int, *, start: datetime | None = None, reach_by: datetime | None = None) -> GroundOption | None:
    """Public transit, or None if it isn't running when the traveler would need it.

    Pass `start` when the departure time is known (leaving an airport), or `reach_by` when the
    ride has to end by a set time (going to an airport). The ride's length then decides when it starts.
    """
    t = rates()["transit"]
    info = t[airport_code]
    if "nj" in place.flags and "from_nj" in info:  # New Jersey travelers reach Newark without going through Manhattan
        info = {**info, **info["from_nj"]}
    hub = (info["hub"]["lat"], info["hub"]["lng"])
    local_miles = geo.haversine_mi(hub, place.point) * 1.2
    walk_only = local_miles <= t["walk_only_miles"]
    local_minutes = local_miles / 3 * 60 if walk_only else local_miles / t["local_mph"] * 60
    minutes = info["hub_minutes"] + local_minutes + t["walk_wait_minutes"]
    if reach_by is not None:
        start = reach_by - timedelta(minutes=round(minutes))
    if not _in_service(start, info["service"]):
        return None
    fare = info["fare"] + (0 if walk_only else info["local_fare"])
    if "nj" in place.flags and "from_nj" not in info and geo.airport(airport_code)["metro"] == "NYC":
        fare += t["nj_to_ny_fare"]  # PATH across the Hudson to reach a New York airport
    label = info["label"] if walk_only or not info["local_fare"] else f"{info['label']} + {info['local_label']}"
    notes = [f"Via {info['hub']['name']}"]
    if checked_bags:
        notes.append("You'll carry your checked bag through stations")
    return GroundOption(
        mode="transit",
        label=label,
        minutes=round(minutes),
        minutes_low=round(minutes * 0.9),
        minutes_high=round(minutes * 1.25),
        low=fare,
        likely=fare,
        high=fare,
        kind="quote",
        source=info["source"],
        notes=notes,
        transfers=1 if walk_only else 2,
    )


def drive_self(place: geo.Place, airport_code: str, when: datetime, routing, to_airport: bool = True) -> GroundOption:
    """Driving your own car one way. Parking is added once per trip by the plan builder."""
    ap = geo.airport(airport_code)
    drive = routing.drive(place.point, (ap["lat"], ap["lng"]), ap["metro"], when)
    card = rates()["rideshare"][ap["metro"]]
    cost = drive.miles * rates()["driving"]["cost_per_mile"] + _toll(card, place, airport_code, to_airport)
    return GroundOption(
        mode="drive",
        label="Drive yourself",
        minutes=round(drive.minutes) + 15,  # parking shuttle
        minutes_low=round(drive.minutes * 0.85) + 10,
        minutes_high=round(drive.minutes * 1.35) + 20,
        low=_r(cost * 0.9),
        likely=_r(cost),
        high=_r(cost * 1.2),
        kind="estimate",
        source=rates()["driving"]["source"],
        notes=["Includes gas and tolls; parking shuttle time added"],
    )
