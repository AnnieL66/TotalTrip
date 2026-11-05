"""The one-click sample trips have to tell the same story on any day someone runs a demo."""
import re
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import brief as B
from app import geo
from app.config import BACKEND_DIR
from app.engine.ground import transit
from app.engine.plans import search_plans
from app.main import app
from app.trips import demo_dates, demo_trips

from .conftest import set_clock

# Every week of a year, so month ends and both clock changes are covered.
DAYS = [date(2026, 11, 4) + timedelta(days=7 * i + i % 7) for i in range(52)]


def _search(monkeypatch, key, day):
    set_clock(monkeypatch, datetime.combine(day, time(16, 0), tzinfo=timezone.utc))
    depart, ret = demo_dates()
    brief, errors = B.apply_updates(
        B.empty_brief(), {**demo_trips()[key]["brief"], "depart_date": depart.isoformat(), "return_date": ret.isoformat()}, "user_said"
    )
    assert not errors
    return search_plans(B.with_defaults(brief))


def _tagged(result, tag):
    return next(p for p in result["plans"] if tag in p["tags"])


def _recommended(result):
    return next(p for p in result["plans"] if p["recommended"])


@pytest.mark.parametrize("day", DAYS)
def test_dates_are_thursday_to_monday_three_weeks_out(monkeypatch, day):
    set_clock(monkeypatch, datetime.combine(day, time(16, 0), tzinfo=timezone.utc))
    depart, ret = demo_dates()
    assert depart.weekday() == 3 and ret.weekday() == 0
    assert 21 <= (depart - day).days < 28


@pytest.mark.parametrize("day", DAYS)
def test_cheaper_newark_flights_cost_more_door_to_door(monkeypatch, day):
    result = _search(monkeypatch, "newark_trap", day)
    cheapest_flights, pick = _tagged(result, "Lowest booking price"), _recommended(result)
    assert cheapest_flights["flights"]["outbound"]["origin"] == "EWR"
    assert pick["flights"]["outbound"]["origin"] == "LGA"
    assert pick["totals"]["bookings"] > cheapest_flights["totals"]["bookings"]
    assert pick["totals"]["likely"] < cheapest_flights["totals"]["likely"]


@pytest.mark.parametrize("day", DAYS)
def test_newark_is_the_right_airport_from_hoboken(monkeypatch, day):
    result = _search(monkeypatch, "newark_fits", day)
    assert {p["flights"]["outbound"]["origin"] for p in result["plans"]} == {"EWR"}


@pytest.mark.parametrize("day", DAYS)
def test_budget_trip_recommends_a_plan_under_budget(monkeypatch, day):
    result = _search(monkeypatch, "tight_budget", day)
    assert _recommended(result)["budget"]["status"] == "under"
    assert _recommended(result)["totals"]["likely"] < _tagged(result, "Lowest booking price")["totals"]["likely"]


def test_homepage_example_matches_the_sample_trip(monkeypatch):
    result = _search(monkeypatch, "newark_trap", DAYS[0])
    page = (BACKEND_DIR.parent / "frontend" / "app" / "page.tsx").read_text()
    shown = {a: (int(f), int(r)) for a, f, r in re.findall(r'airport: "(\w+)", flights: (\d+), rides: (\d+)', page)}
    for airport, plan in (("Newark", _tagged(result, "Lowest booking price")), ("LaGuardia", _recommended(result))):
        assert shown[airport] == (plan["totals"]["bookings"], plan["totals"]["getting_around"])


def test_new_jersey_travelers_reach_newark_without_going_through_manhattan():
    start = datetime(2026, 12, 3, 12, 0, tzinfo=timezone.utc)
    assert "Newark Penn Station" in transit(geo.get_place("hoboken"), "EWR", 0, start=start).notes[0]
    assert "Penn Station" in transit(geo.get_place("upper_west_side"), "EWR", 0, start=start).notes[0]
    assert "Newark" not in transit(geo.get_place("upper_west_side"), "EWR", 0, start=start).notes[0]


@pytest.mark.parametrize("key", ["newark_trap", "newark_fits", "tight_budget"])
def test_sample_trip_starts_ready_to_compare(key):
    with TestClient(app) as client:
        trip = client.post(f"/api/trips/demo/{key}").json()
        assert trip["brief"]["ready"] and trip["plans"] is None
        assert [m["role"] for m in trip["messages"]] == ["user", "assistant"]
        assert client.post(f"/api/trips/{trip['id']}/plans").json()["plans"]


def test_unknown_sample_trip_is_not_found():
    with TestClient(app) as client:
        assert client.post("/api/trips/demo/made-up").status_code == 404
