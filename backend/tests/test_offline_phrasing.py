"""Ways testers actually phrase a trip, which the offline assistant used to ignore or misread."""
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import brief as B
from app.ai.offline import extract
from app.main import app

from .conftest import set_clock

WEDNESDAY = date(2026, 11, 4)


def _extract(text, current=None):
    return extract(text, current or B.with_defaults(B.empty_brief()), today=WEDNESDAY)[0]


def _with_depart(day):
    brief, errors = B.apply_updates(B.empty_brief(), {"depart_date": day}, "user_said")
    assert not errors
    return B.with_defaults(brief)


@pytest.mark.parametrize(
    "text, depart, ret",
    [
        ("leaving tomorrow", "2026-11-05", None),
        ("the day after tomorrow", "2026-11-06", None),
        ("flying today", "2026-11-04", None),
        ("friday", "2026-11-06", None),
        ("leaving this friday", "2026-11-06", None),
        ("next friday", "2026-11-13", None),
        ("friday to sunday", "2026-11-06", "2026-11-08"),
        ("next friday to sunday", "2026-11-13", "2026-11-15"),
        ("leaving friday, back sunday", "2026-11-06", "2026-11-08"),
        ("this weekend", "2026-11-06", "2026-11-08"),
        ("next weekend", "2026-11-13", "2026-11-15"),
        ("tomorrow to sunday", "2026-11-05", "2026-11-08"),
        ("dec 8 to friday", "2026-12-08", "2026-12-11"),
        ("2026-12-08 to 2026-12-12", "2026-12-08", "2026-12-12"),
        ("12 dec to 16 dec", "2026-12-12", "2026-12-16"),
        ("8th of december to 12th of december", "2026-12-08", "2026-12-12"),
        ("december 8th through the 12th", "2026-12-08", "2026-12-12"),
        ("nov 30 dec 2", "2026-11-30", "2026-12-02"),
        ("Friday, Dec 11 to Sunday, Dec 13", "2026-12-11", "2026-12-13"),
        ("friday 12/11 to sunday 12/13", "2026-12-11", "2026-12-13"),
        ("leaving friday dec 11 back sunday dec 13", "2026-12-11", "2026-12-13"),
        ("this friday, dec 11 through sunday, dec 13", "2026-12-11", "2026-12-13"),
        ("dec 11, friday to dec 13, sunday", "2026-12-11", "2026-12-13"),
        ("friday 11 dec to sunday 13 dec", "2026-12-11", "2026-12-13"),
        ("12-15 dec", "2026-12-12", "2026-12-15"),
        ("12 to 15 dec", "2026-12-12", "2026-12-15"),
        ("12th-15th of december", "2026-12-12", "2026-12-15"),
        ("dec 12 to 15 jan", "2026-12-12", "2027-01-15"),
    ],
)
def test_date_phrasings(text, depart, ret):
    fields = _extract(text)
    assert fields["depart_date"] == depart
    assert fields.get("return_date") == ret


def test_back_on_a_weekday_counts_from_the_departure():
    fields = _extract("back sunday", _with_depart("2026-12-08"))
    assert fields == {"return_date": "2026-12-13"}


def test_leaving_on_a_weekday_counts_from_today_not_the_old_departure():
    assert _extract("leaving friday", _with_depart("2026-12-08"))["depart_date"] == "2026-11-06"


@pytest.mark.parametrize("text", ["I work fridays", "2030-01-01", "2026-13-45", "2020-01-01", "a sundae", "free to go today-ish later"])
def test_things_that_are_not_trip_dates_stay_ignored(text):
    fields = _extract(text)
    assert "return_date" not in fields
    if "today" not in text:
        assert "depart_date" not in fields


@pytest.mark.parametrize(
    "text, ret",
    [
        ("dec 8 for 3 days", "2026-12-10"),
        ("dec 8, a 3-day trip", "2026-12-10"),
        ("dec 8 for one day", "2026-12-09"),
        ("dec 8 for a weekend", "2026-12-10"),
        ("dec 8 for a long weekend", "2026-12-11"),
        ("dec 8 for 4 nights", "2026-12-12"),
    ],
)
def test_trip_lengths(text, ret):
    assert _extract(text)["return_date"] == ret


@pytest.mark.parametrize(
    "text, expected",
    [
        ("I have no bags", {"carry_on": 0, "checked_bags": 0}),
        ("no luggage", {"carry_on": 0, "checked_bags": 0}),
        ("I'm bringing 2 suitcases", {"checked_bags": 2}),
        ("a big suitcase", {"checked_bags": 1}),
        ("no bags to check, just a carry-on", {"carry_on": 1, "checked_bags": 0}),
        ("no bags just a carry on", {"carry_on": 1, "checked_bags": 0}),
        ("no bags to check", {"checked_bags": 0}),
        ("no bag fees please", {}),
        ("no carry-on, no bags", {"carry_on": 0, "checked_bags": 0}),
    ],
)
def test_bag_phrasings(text, expected):
    assert _extract(text) == expected


@pytest.mark.parametrize("text", ["the flight was $300 last time", "the ride cost $50", "I paid $200", "it costs $90"])
def test_past_prices_are_not_budgets(text):
    assert "budget" not in _extract(text)


@pytest.mark.parametrize("text, amount", [("$500", 500), ("keep it to $400 total", 400), ("under $900", 900)])
def test_dollar_budgets_still_read(text, amount):
    assert _extract(text)["budget"] == amount


def test_lets_go_to_a_place_does_not_start_a_search():
    with TestClient(app) as client:
        trip_id = client.post("/api/trips", json={}).json()["id"]
        r = client.post(f"/api/trips/{trip_id}/messages", json={"text": "Let's go to Wicker Park from Harlem Dec 8-12"}).json()
        assert r["brief"]["ready"] and not r["plans_searched"]
        assert client.post(f"/api/trips/{trip_id}/messages", json={"text": "let's go"}).json()["plans_searched"]


def test_chat_warns_when_explaining_plans_that_are_out_of_date():
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={"message": "Harlem to Wicker Park, Nov 12 to Nov 16"}).json()
        client.post(f"/api/trips/{trip['id']}/plans")
        current = client.post(f"/api/trips/{trip['id']}/messages", json={"text": "Why is the recommended plan better?"}).json()
        assert "out of date" not in current["reply"]
        client.patch(f"/api/trips/{trip['id']}/brief", json={"checked_bags": 2})
        stale = client.post(f"/api/trips/{trip['id']}/messages", json={"text": "Why is the recommended plan better?"}).json()
        assert "out of date" in stale["reply"]


def test_a_full_sentence_fills_the_brief(monkeypatch):
    set_clock(monkeypatch, datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc))
    with TestClient(app) as client:
        text = "Upper West Side to Wicker Park next Friday for 3 days with 1 suitcase, under $600"
        fields = {f["name"]: f["value"] for f in client.post("/api/trips", json={"message": text}).json()["brief"]["fields"]}
        assert fields["depart_date"] == "2026-11-13" and fields["return_date"] == "2026-11-15"
        assert fields["checked_bags"] == 1 and fields["budget"] == 600
