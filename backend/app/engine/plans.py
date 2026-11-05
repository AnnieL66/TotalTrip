"""The cost engine: builds complete door-to-door plans and ranks them.

Deterministic and rule-based. The AI never produces a number that appears here.
"""
from __future__ import annotations

import itertools
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from .. import clock, geo
from ..adapters.flights import FlightOffer, FlightSearchError, get_flight_adapter, search_cached
from ..adapters.routing import get_routing
from ..brief import missing_required
from .ground import GroundOption, drive_self, rideshare, transit
from .rates import bag_fees, rates

log = logging.getLogger(__name__)

WEIGHTS = {
    # $ value of one minute door to door, $ per transfer, $ for leaving home before 5:30 am
    "cheapest": {"per_min": 0.05, "transfer": 2, "early": 10, "bag_on_transit": 3},
    "balanced": {"per_min": 0.40, "transfer": 8, "early": 30, "bag_on_transit": 10},
    "easiest": {"per_min": 0.80, "transfer": 30, "early": 60, "bag_on_transit": 30},
}
OVER_BUDGET_PENALTY = 1000
MAX_OFFERS_PER_DIRECTION = 8
FARE_CLASS_LABELS = {"main": "Main cabin", "basic": "Basic economy"}


@dataclass
class TripContext:
    origin: geo.Place
    destination: geo.Place
    depart: date
    ret: date
    carry_on: int
    checked: int
    budget: float | None
    priority: str
    arrive_by: time | None
    will_drive: bool

    @property
    def nights(self) -> int:
        return (self.ret - self.depart).days

    @property
    def weights(self) -> dict:
        return WEIGHTS[self.priority]


def context_from_brief(brief: dict) -> TripContext:
    missing = missing_required(brief)
    if missing:
        raise ValueError(f"Trip brief is missing: {', '.join(missing)}")
    v = {k: f["value"] for k, f in brief.items()}
    return TripContext(
        origin=geo.get_place(v["origin"]),
        destination=geo.get_place(v["destination"]),
        depart=date.fromisoformat(v["depart_date"]),
        ret=date.fromisoformat(v["return_date"]),
        carry_on=v["carry_on"] if v["carry_on"] is not None else 1,
        checked=v["checked_bags"] or 0,
        budget=v["budget"],
        priority=v["priority"] or "balanced",
        arrive_by=time.fromisoformat(v["arrive_by"]) if v["arrive_by"] else None,
        will_drive=bool(v["will_drive"]),
    )


# ---------------------------------------------------------------- bags


@dataclass
class BagCost:
    amount: float
    detail: str
    effective_checked: int
    notes: list[str]


def bag_cost(offer: FlightOffer, carry_on: int, checked: int) -> BagCost:
    fees = bag_fees()["fees"][offer.airline][offer.fare_class]
    notes: list[str] = []
    amount = 0.0
    n_checked = checked
    if carry_on:
        if fees["carry_on"] is None:
            n_checked += 1
            notes.append(f"{offer.airline_name} basic economy doesn't include a full-size carry-on, so we priced it as a checked bag")
        else:
            amount += fees["carry_on"]
    if n_checked >= 1:
        amount += fees["checked_1"]
    if n_checked >= 2:
        amount += fees["checked_2"] * (n_checked - 1)
    parts = []
    if carry_on:
        parts.append("carry-on" + (" (as checked)" if fees["carry_on"] is None else ""))
    if checked:
        parts.append(f"{checked} checked")
    detail = ", ".join(parts) if parts else "personal item only"
    return BagCost(amount, detail, n_checked, notes)


def _buffer_minutes(n_checked: int) -> int:
    return 90 if n_checked else 60


def _deplane_minutes(n_checked: int) -> int:
    return 35 if n_checked else 15


# ---------------------------------------------------------------- candidates


@dataclass
class Leg:
    key: str
    label: str
    option: GroundOption
    start: datetime
    end: datetime


@dataclass
class Candidate:
    out: FlightOffer
    back: FlightOffer
    out_bags: BagCost
    back_bags: BagCost
    legs: list[Leg]
    parking: float
    strategy: str
    score: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def bookings(self) -> float:
        return self.out.fare + self.back.fare + self.out_bags.amount + self.back_bags.amount

    @property
    def ground_likely(self) -> float:
        return sum(l.option.likely for l in self.legs) + self.parking

    @property
    def ground_low(self) -> float:
        return sum(l.option.low for l in self.legs) + self.parking

    @property
    def ground_high(self) -> float:
        return sum(l.option.high for l in self.legs) + self.parking

    @property
    def total(self) -> float:
        return self.bookings + self.ground_likely

    @property
    def transfers(self) -> int:
        return sum(l.option.transfers for l in self.legs)

    @property
    def door_out(self) -> int:
        return int((self.legs[1].end - self.legs[0].start).total_seconds() // 60)

    @property
    def door_back(self) -> int:
        return int((self.legs[3].end - self.legs[2].start).total_seconds() // 60)

    @property
    def signature(self) -> tuple:
        return (self.out.id, self.back.id, tuple(l.option.mode for l in self.legs))


def _home_leg_options(ctx: TripContext, airport_code: str, routing, *, start: datetime, reach_by: datetime | None = None) -> list[GroundOption]:
    """A traveler who chose to drive drives: the car goes to the airport and back, so it's the only option at home."""
    if ctx.will_drive:
        return [drive_self(ctx.origin, airport_code, start, routing)]
    return _leg_options(ctx, ctx.origin, airport_code, routing, start=start, reach_by=reach_by)


def _leg_options(ctx: TripContext, place: geo.Place, airport_code: str, routing, *, start: datetime, reach_by: datetime | None = None) -> list[GroundOption]:
    """`start` prices the ride; `reach_by` is when a ride to the airport has to arrive, if that's what sets its start."""
    opts = [rideshare(place, airport_code, start, routing)]
    t = transit(place, airport_code, ctx.checked, start=start, reach_by=reach_by)
    if t:
        opts.append(t)
    return opts


def _option_cost(ctx: TripContext, opt: GroundOption) -> float:
    w = ctx.weights
    cost = opt.likely + w["per_min"] * opt.minutes + w["transfer"] * opt.transfers
    if opt.mode == "transit" and ctx.checked:
        cost += w["bag_on_transit"]
    return cost


def _pick(ctx: TripContext, pairs, strategy: str):
    if strategy == "rideshare":
        pairs = [p for p in pairs if p[0].mode == "rideshare" and p[1].mode == "rideshare"] or pairs
    elif strategy == "transit":
        ranked = sorted(pairs, key=lambda p: -sum(o.mode == "transit" for o in p))
        most = sum(o.mode == "transit" for o in ranked[0])
        pairs = [p for p in ranked if sum(o.mode == "transit" for o in p) == most]
    return min(pairs, key=lambda p: sum(_option_cost(ctx, o) for o in p))


def _build_candidates(ctx: TripContext, outs: list[FlightOffer], backs: list[FlightOffer], routing, now: datetime) -> list[Candidate]:
    candidates: dict[tuple, Candidate] = {}
    o_name = lambda code: geo.airport(code)["name"]  # noqa: E731
    for out, back in itertools.product(outs, backs):
        ob = bag_cost(out, ctx.carry_on, ctx.checked)
        bb = bag_cost(back, ctx.carry_on, ctx.checked)
        out_buffer, back_buffer = _buffer_minutes(ob.effective_checked), _buffer_minutes(bb.effective_checked)
        approx_home_start = out.depart_dt - timedelta(minutes=out_buffer + 45)
        arr_start = out.arrive_dt + timedelta(minutes=_deplane_minutes(ob.effective_checked))
        approx_dest_start = back.depart_dt - timedelta(minutes=back_buffer + 45)
        home_start = back.arrive_dt + timedelta(minutes=_deplane_minutes(bb.effective_checked))

        leg1 = _home_leg_options(ctx, out.origin, routing, start=approx_home_start, reach_by=out.depart_dt - timedelta(minutes=out_buffer))
        leg4 = _home_leg_options(ctx, back.destination, routing, start=home_start)
        leg2 = _leg_options(ctx, ctx.destination, out.destination, routing, start=arr_start)
        leg3 = _leg_options(ctx, ctx.destination, back.origin, routing, start=approx_dest_start, reach_by=back.depart_dt - timedelta(minutes=back_buffer))
        origin_pairs, dest_pairs = list(itertools.product(leg1, leg4)), list(itertools.product(leg2, leg3))

        for strategy in ("best", "rideshare", "transit"):
            o1, o4 = _pick(ctx, origin_pairs, strategy)
            o2, o3 = _pick(ctx, dest_pairs, strategy)
            parking = 0.0
            if o1.mode == "drive":
                parking = rates()["driving"]["parking_per_day"][out.origin] * (ctx.nights + 1)
            leave_home = out.depart_dt - timedelta(minutes=out_buffer + o1.minutes)
            if leave_home <= now:
                continue  # the traveler would already have had to leave
            leave_dest = back.depart_dt - timedelta(minutes=back_buffer + o3.minutes)
            legs = [
                Leg("home_to_airport", f"Home → {o_name(out.origin)}", o1, leave_home, leave_home + timedelta(minutes=o1.minutes)),
                Leg("airport_to_destination", f"{o_name(out.destination)} → {ctx.destination.name.split(',')[0]}", o2, arr_start, arr_start + timedelta(minutes=o2.minutes)),
                Leg("destination_to_airport", f"{ctx.destination.name.split(',')[0]} → {o_name(back.origin)}", o3, leave_dest, leave_dest + timedelta(minutes=o3.minutes)),
                Leg("airport_to_home", f"{o_name(back.destination)} → Home", o4, home_start, home_start + timedelta(minutes=o4.minutes)),
            ]
            cand = Candidate(out, back, ob, bb, legs, parking, strategy)
            if cand.signature not in candidates:
                _score(ctx, cand)
                candidates[cand.signature] = cand
    return list(candidates.values())


def _score(ctx: TripContext, c: Candidate) -> None:
    w = ctx.weights
    score = c.total + w["per_min"] * (c.door_out + c.door_back) + w["transfer"] * c.transfers
    for leg in c.legs:
        if leg.option.mode == "transit" and ctx.checked:
            score += w["bag_on_transit"]
    for leg, where in ((c.legs[0], "home"), (c.legs[2], "Chicago")):
        if leg.start.time() < time(5, 30):
            score += w["early"]
            c.warnings.append(f"You'd leave {where} at {_fmt_time(leg.start)}")
    arrive_home = c.legs[3].end
    if arrive_home.date() > ctx.ret:
        score += w["early"]
        c.warnings.append(f"You'd get home after midnight ({_fmt_time(arrive_home)})")
    if ctx.budget and c.total > ctx.budget:
        score += OVER_BUDGET_PENALTY + (c.total - ctx.budget)
    c.score = score


# ---------------------------------------------------------------- search


def _fetch(adapter, origin: str, destination: str, day: date, refresh: bool, notes: list[str]) -> list[FlightOffer]:
    try:
        return search_cached(adapter, origin, destination, day, refresh=refresh)
    except FlightSearchError as exc:
        log.warning("%s", exc)
        notes.append(f"Couldn't refresh {origin}→{destination} flights just now; that route is left out.")
        return []


def _prefilter(ctx: TripContext, offers: list[FlightOffer], now: datetime) -> list[FlightOffer]:
    offers = [o for o in offers if o.airline in bag_fees()["fees"] and o.fare_class in bag_fees()["fees"][o.airline] and o.depart_dt > now]
    offers.sort(key=lambda o: o.fare + bag_cost(o, ctx.carry_on, ctx.checked).amount)
    return offers[:MAX_OFFERS_PER_DIRECTION]


def search_plans(brief: dict, refresh: bool = False) -> dict:
    ctx = context_from_brief(brief)
    adapter, routing = get_flight_adapter(), get_routing()
    now = clock.now()
    notes: list[str] = []
    candidates: list[Candidate] = []
    relaxed_arrival = False

    for o_code in geo.metro_airports(ctx.origin.metro):
        for d_code in geo.metro_airports(ctx.destination.metro):
            outs = _fetch(adapter, o_code, d_code, ctx.depart, refresh, notes)
            backs = _fetch(adapter, d_code, o_code, ctx.ret, refresh, notes)
            if not outs or not backs:
                continue
            candidates += _build_candidates(ctx, _prefilter(ctx, outs, now), _prefilter(ctx, backs, now), routing, now)

    if not candidates and ctx.depart <= clock.today():
        notes.append("Today's remaining flights have left or leave too soon to reach. Try a later date.")

    if ctx.arrive_by and candidates:
        on_time = [c for c in candidates if c.out.arrive_dt.time() <= ctx.arrive_by]
        if on_time:
            candidates = on_time
        else:
            relaxed_arrival = True
            notes.append(f"No flights land by {_fmt_time_t(ctx.arrive_by)}, so these plans land later.")

    plans, comparison = _select(ctx, candidates)
    return {
        "search_id": uuid.uuid4().hex[:12],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_mode": adapter.name,
        "candidates_considered": len(candidates),
        "plans": plans,
        "comparison": comparison,
        "notes": notes,
        "relaxed_arrival": relaxed_arrival,
    }


def _select(ctx: TripContext, candidates: list[Candidate]) -> tuple[list[dict], dict | None]:
    if not candidates:
        return [], None
    best_strategy = [c for c in candidates if c.strategy == "best"] or candidates
    lowest_booking = min(best_strategy, key=lambda c: (c.bookings, c.score))
    best_fit = min(candidates, key=lambda c: c.score)
    lowest_total = min(candidates, key=lambda c: (c.total, c.score))
    fastest = min(candidates, key=lambda c: (c.door_out + c.door_back, c.total))

    picks: list[tuple[Candidate, list[str]]] = []

    def add(c: Candidate, tag: str) -> None:
        for existing, tags in picks:
            if existing.signature == c.signature:
                tags.append(tag)
                return
        picks.append((c, [tag]))

    add(lowest_booking, "Lowest booking price")
    add(best_fit, "Best fit")
    add(lowest_total, "Lowest total cost")
    if len(picks) < 3 and (best_fit.door_out + best_fit.door_back) - (fastest.door_out + fastest.door_back) >= 30:
        add(fastest, "Least travel time")
    picks = picks[:3]

    plans = [_plan_view(ctx, c, chr(ord("A") + i), tags, recommended=c.signature == best_fit.signature) for i, (c, tags) in enumerate(picks)]
    by_sig = {c.signature: p for (c, _), p in zip(picks, plans)}
    return plans, _comparison(by_sig[lowest_booking.signature], by_sig[best_fit.signature], plans)


# ---------------------------------------------------------------- presentation


def _fmt_time(dt: datetime) -> str:
    return _fmt_time_t(dt.time())


def _fmt_time_t(t: time) -> str:
    h12 = t.hour % 12 or 12
    suffix = "am" if t.hour < 12 else "pm"
    return f"{h12}:{t.minute:02d} {suffix}"


def _fmt_minutes(m: int) -> str:
    h, mm = divmod(int(m), 60)
    return f"{h}h {mm:02d}m" if h else f"{mm} min"


def _money(x: float) -> float:
    return float(round(x))


def _flight_view(o: FlightOffer, bags: BagCost) -> dict:
    return {
        "id": o.id,
        "airline": o.airline_name,
        "flight_number": o.flight_number,
        "origin": o.origin,
        "origin_name": geo.airport(o.origin)["name"],
        "destination": o.destination,
        "destination_name": geo.airport(o.destination)["name"],
        "depart": o.depart,
        "arrive": o.arrive,
        "depart_label": _fmt_time(o.depart_dt),
        "arrive_label": _fmt_time(o.arrive_dt),
        "duration_min": o.duration_min,
        "fare_class": FARE_CLASS_LABELS[o.fare_class],
        "fare": o.fare,
        "bags": bags.amount,
        "bags_detail": bags.detail,
        "source": o.source,
        "as_of": o.as_of,
    }


def _convenience(c: Candidate) -> dict:
    door = c.door_out + c.door_back
    effort = door + 15 * c.transfers + 30 * len(c.warnings)
    rating = "High" if effort <= 700 else "Medium" if effort <= 850 else "Low"
    worst_leg = max(c.legs, key=lambda l: l.option.minutes)
    return {
        "rating": rating,
        "door_to_door_out": _fmt_minutes(c.door_out),
        "door_to_door_back": _fmt_minutes(c.door_back),
        "summary": f"{_fmt_minutes(c.door_out)} door to door going, {_fmt_minutes(c.door_back)} coming home. Longest ride: {worst_leg.label} ({worst_leg.option.label.lower()}, ~{worst_leg.option.minutes} min).",
    }


def _title(c: Candidate) -> str:
    o, d = geo.airport(c.out.origin)["name"], geo.airport(c.out.destination)["name"]
    modes = {l.option.mode for l in c.legs}
    if modes == {"transit"}:
        how = "transit both ends"
    elif modes == {"rideshare"}:
        how = "rides both ends"
    elif "drive" in modes:
        how = "drive and park"
    else:
        how = "mix of rides and transit"
    return f"Fly {o} → {d}, {how}"


def _plan_view(ctx: TripContext, c: Candidate, letter: str, tags: list[str], recommended: bool) -> dict:
    items = []
    for direction, offer, bags in (("Going", c.out, c.out_bags), ("Return", c.back, c.back_bags)):
        items.append({
            "group": "bookings", "category": "flight",
            "label": f"Flight {offer.origin} → {offer.destination}",
            "detail": f"{offer.airline_name} {offer.flight_number} · {FARE_CLASS_LABELS[offer.fare_class]} · {_fmt_time(offer.depart_dt)}–{_fmt_time(offer.arrive_dt)}",
            "low": offer.fare, "likely": offer.fare, "high": offer.fare,
            "kind": offer.kind, "source": offer.source, "as_of": offer.as_of,
        })
    bag_total = c.out_bags.amount + c.back_bags.amount
    items.append({
        "group": "bookings", "category": "bags",
        "label": "Bags, both flights",
        "detail": f"{c.out_bags.detail.capitalize()} going; {c.back_bags.detail} coming back" if c.out_bags.detail != c.back_bags.detail else f"{c.out_bags.detail.capitalize()}, each way",
        "low": bag_total, "likely": bag_total, "high": bag_total,
        "kind": "quote", "source": bag_fees()["source"], "as_of": bag_fees()["as_of"],
    })
    for leg in c.legs:
        o = leg.option
        items.append({
            "group": "getting_around", "category": o.mode,
            "label": leg.label,
            "detail": f"{o.label} · {o.minutes_low}–{o.minutes_high} min · leave {_fmt_time(leg.start)}",
            "low": o.low, "likely": o.likely, "high": o.high,
            "kind": o.kind, "source": o.source, "as_of": rates()["as_of"],
            "notes": o.notes,
        })
    if c.parking:
        items.append({
            "group": "getting_around", "category": "parking",
            "label": f"Parking at {geo.airport(c.out.origin)['name']}",
            "detail": f"{ctx.nights + 1} days",
            "low": c.parking, "likely": c.parking, "high": c.parking,
            "kind": "estimate", "source": rates()["driving"]["source"], "as_of": rates()["as_of"],
        })

    total, low, high = _money(c.total), _money(c.bookings + c.ground_low), _money(c.bookings + c.ground_high)
    budget = None
    if ctx.budget:
        if high <= ctx.budget:
            budget = {"status": "under", "amount": _money(ctx.budget - total), "message": f"${_money(ctx.budget - total):,.0f} under budget, even at the high end"}
        elif total <= ctx.budget:
            budget = {"status": "tight", "amount": _money(ctx.budget - total), "message": f"${_money(ctx.budget - total):,.0f} under budget at typical prices; could go over at the high end"}
        else:
            budget = {"status": "over", "amount": _money(total - ctx.budget), "message": f"About ${_money(total - ctx.budget):,.0f} over budget"}

    warnings = list(dict.fromkeys(c.warnings + c.out_bags.notes + c.back_bags.notes))
    if any(l.option.mode == "transit" for l in c.legs) and ctx.checked:
        warnings.append("Transit legs mean carrying your checked bag through stations")
    airlines = {c.out.airline_name: c.out.booking_url, c.back.airline_name: c.back.booking_url}

    return {
        "id": letter,
        "letter": letter,
        "tags": tags,
        "recommended": recommended,
        "title": _title(c),
        "flights": {"outbound": _flight_view(c.out, c.out_bags), "return": _flight_view(c.back, c.back_bags)},
        "legs": [
            {"key": l.key, "label": l.label, "mode": l.option.mode, "mode_label": l.option.label,
             "start": l.start.isoformat(), "start_label": _fmt_time(l.start), "end_label": _fmt_time(l.end),
             "minutes": l.option.minutes, "minutes_low": l.option.minutes_low, "minutes_high": l.option.minutes_high,
             "low": l.option.low, "likely": l.option.likely, "high": l.option.high, "kind": l.option.kind, "notes": l.option.notes}
            for l in c.legs
        ],
        "items": items,
        "totals": {
            "bookings": _money(c.bookings),
            "getting_around": _money(c.ground_likely),
            "likely": total,
            "low": low,
            "high": high,
        },
        "door_to_door": {
            "outbound_minutes": c.door_out,
            "return_minutes": c.door_back,
            "leave_home": _fmt_time(c.legs[0].start),
            "arrive_destination": _fmt_time(c.legs[1].end),
            "leave_destination": _fmt_time(c.legs[2].start),
            "arrive_home": _fmt_time(c.legs[3].end),
        },
        "convenience": _convenience(c),
        "budget": budget,
        "warnings": warnings,
        "booking_links": [{"label": f"{name} site", "url": url} for name, url in airlines.items()],
    }


def _comparison(cheapest_booking: dict, best: dict, plans: list[dict]) -> dict:
    if cheapest_booking["id"] == best["id"]:
        others = [p for p in plans if p["id"] != best["id"]]
        detail = ""
        if others:
            alt = min(others, key=lambda p: p["door_to_door"]["outbound_minutes"] + p["door_to_door"]["return_minutes"])
            saved = (alt["door_to_door"]["outbound_minutes"] + alt["door_to_door"]["return_minutes"]) - (best["door_to_door"]["outbound_minutes"] + best["door_to_door"]["return_minutes"])
            diff = alt["totals"]["likely"] - best["totals"]["likely"]
            if saved < 0 and diff > 0:
                detail = f"Plan {alt['id']} saves {_fmt_minutes(-saved)} of travel time for about ${diff:,.0f} more."
        return {"headline": f"Here, the cheapest flights are also the best whole trip: Plan {best['id']}.", "detail": detail, "recommended": best["id"]}

    a, b = cheapest_booking, best
    more_booking = b["totals"]["bookings"] - a["totals"]["bookings"]
    less_ground = a["totals"]["getting_around"] - b["totals"]["getting_around"]
    total_diff = a["totals"]["likely"] - b["totals"]["likely"]
    time_diff = (a["door_to_door"]["outbound_minutes"] + a["door_to_door"]["return_minutes"]) - (b["door_to_door"]["outbound_minutes"] + b["door_to_door"]["return_minutes"])

    if more_booking > 0 and less_ground > 0:
        headline = f"Spend ${more_booking:,.0f} more on flights. Save about ${less_ground:,.0f} getting around."
    elif more_booking > 0:
        headline = f"Spend ${more_booking:,.0f} more on flights for an easier trip."
    else:
        headline = f"Plan {b['id']} is the better whole trip."

    parts = []
    if total_diff > 0:
        parts.append(f"At typical prices, Plan {b['id']} is about ${total_diff:,.0f} less overall")
    elif total_diff < 0:
        parts.append(f"Plan {b['id']} costs about ${-total_diff:,.0f} more overall")
    else:
        parts.append(f"Both plans cost about the same overall")
    if time_diff >= 10:
        parts.append(f"saves {_fmt_minutes(time_diff)} door to door")
    elif time_diff <= -10:
        parts.append(f"takes {_fmt_minutes(-time_diff)} longer door to door")
    detail = (parts[0] + (" and " + parts[1] if len(parts) > 1 else "") + ".")
    return {"headline": headline, "detail": detail, "recommended": b["id"], "cheapest_flights_plan": a["id"]}
