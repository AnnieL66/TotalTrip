from datetime import date, datetime, time

from app import brief as B
from app import geo
from app.adapters.flights import FixtureFlights
from app.adapters.routing import EstimateRouting
from app.engine.ground import rideshare
from app.engine.plans import bag_cost, search_plans


def _search(b, **updates):
    if updates:
        b, errors = B.apply_updates(b, updates, "user_said")
        assert not errors
    return search_plans(b)


def test_plans_are_internally_consistent(sample_brief):
    result = _search(sample_brief)
    assert 2 <= len(result["plans"]) <= 3
    assert result["comparison"]["recommended"] in {p["id"] for p in result["plans"]}
    for plan in result["plans"]:
        items = plan["items"]
        assert round(sum(i["likely"] for i in items)) == plan["totals"]["likely"]
        assert round(sum(i["likely"] for i in items if i["group"] == "bookings")) == plan["totals"]["bookings"]
        assert plan["totals"]["low"] <= plan["totals"]["likely"] <= plan["totals"]["high"]
        for i in items:
            assert i["kind"] in {"quote", "estimate", "demo"}
            assert i["low"] <= i["likely"] <= i["high"]
            if i["kind"] in {"quote", "demo"}:
                assert i["low"] == i["high"]
        assert len(plan["legs"]) == 4


def test_deterministic_demo_prices(sample_brief):
    a, b = _search(sample_brief), _search(sample_brief)
    assert [p["totals"] for p in a["plans"]] == [p["totals"] for p in b["plans"]]


def test_arrive_by_constraint(sample_brief):
    result = _search(sample_brief, arrive_by="12:00")
    assert not result["relaxed_arrival"]
    for plan in result["plans"]:
        landing = plan["flights"]["outbound"]["arrive"]
        assert time.fromisoformat(landing[11:16]) <= time(12, 0)


def test_impossible_arrival_is_relaxed_and_disclosed(sample_brief):
    result = _search(sample_brief, arrive_by="06:00")
    assert result["relaxed_arrival"]
    assert any("land by" in n for n in result["notes"])


def test_budget_status(sample_brief):
    tight = _search(sample_brief, budget=150)
    assert all(p["budget"]["status"] == "over" for p in tight["plans"])
    roomy = _search(sample_brief, budget=2000)
    assert all(p["budget"]["status"] == "under" for p in roomy["plans"])


def test_priority_changes_the_recommendation(sample_brief):
    cheap = _search(sample_brief, priority="cheapest", checked_bags=1)
    easy = _search(sample_brief, priority="easiest", checked_bags=1)
    rec = lambda r: next(p for p in r["plans"] if p["recommended"])  # noqa: E731
    assert rec(cheap)["totals"]["likely"] <= rec(easy)["totals"]["likely"]
    door = lambda p: p["door_to_door"]["outbound_minutes"] + p["door_to_door"]["return_minutes"]  # noqa: E731
    assert door(rec(easy)) <= door(rec(cheap))


def test_driving_adds_parking_once(sample_brief):
    result = _search(sample_brief, will_drive=True, priority="easiest")
    for plan in result["plans"]:
        modes = [l["mode"] for l in plan["legs"]]
        parking = [i for i in plan["items"] if i["category"] == "parking"]
        assert modes[0] == modes[3] == "drive"
        assert len(parking) == 1


def test_basic_economy_without_carry_on_is_priced_as_checked():
    offer = next(o for o in FixtureFlights().search("EWR", "ORD", date(2026, 11, 12)) if o.airline == "UA" and o.fare_class == "basic")
    bags = bag_cost(offer, carry_on=1, checked=0)
    assert bags.amount == 45 and bags.effective_checked == 1 and bags.notes


def test_cheapest_flight_is_not_always_cheapest_trip(sample_brief):
    """From the Upper West Side, the Newark rides (tolls, distance) should cost more than LaGuardia's."""
    result = _search(sample_brief, priority="easiest")
    when = datetime(2026, 11, 12, 11, 0)
    uws = geo.get_place("upper_west_side")
    ewr = rideshare(uws, "EWR", when, EstimateRouting())
    lga = rideshare(uws, "LGA", when, EstimateRouting())
    assert ewr.likely > lga.likely + 15
    assert result["comparison"]["headline"]
