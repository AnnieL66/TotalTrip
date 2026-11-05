"""One test per bug found in the code audit, so each stays fixed."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text

from app import brief as B
from app import geo
from app.adapters.routing import EstimateRouting
from app.ai.offline import extract
from app.db import SessionLocal, SpendReport, add_missing_columns, log_event
from app.engine.ground import drive_self, rideshare
from app.engine.plans import search_plans
from app.engine.rates import rates
from app.main import app

from .conftest import ADMIN, set_clock

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 17)
ROUTE = "Upper West Side to Millennium Park"


def _brief(**updates):
    base = {"origin": "Upper West Side", "destination": "Millennium Park", "depart_date": "2026-11-12", "return_date": "2026-11-16"}
    b, errors = B.apply_updates(B.empty_brief(), {**base, **updates}, "user_said")
    assert not errors
    return B.with_defaults(b)


def _fields(state):
    return {f["name"]: f["value"] for f in state["brief"]["fields"]}


# ---------------------------------------------------------------- mock flight data


def test_demo_fares_are_never_labeled_as_quotes():
    result = search_plans(_brief())
    assert result["data_mode"] == "demo"
    for plan in result["plans"]:
        flights = [i for i in plan["items"] if i["category"] == "flight"]
        assert flights and all(i["kind"] == "demo" for i in flights)


def test_chat_says_flight_prices_are_demo_fares():
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={"message": f"{ROUTE}, Nov 12 to Nov 16"}).json()
        r = client.post(f"/api/trips/{trip['id']}/messages", json={"text": "compare plans"}).json()
        assert "demo fares" in r["reply"] and "flights and bag fees are quotes" not in r["reply"]


def test_mock_data_has_no_defunct_airline():
    assert "NK" not in geo.airport_data()["airlines"]
    for plan in search_plans(_brief())["plans"]:
        assert "Spirit" not in (plan["flights"]["outbound"]["airline"], plan["flights"]["return"]["airline"])


# ---------------------------------------------------------------- return before departure


def test_rejected_date_range_keeps_both_old_dates(sample_brief):
    b, errors = B.apply_updates(sample_brief, {"depart_date": "2026-12-24", "return_date": "2026-12-22"}, "user_said")
    assert errors
    assert b["depart_date"]["value"] == "2026-11-12"
    assert b["return_date"]["value"] == "2026-11-16"


def test_chat_cannot_invert_the_dates():
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={"message": f"{ROUTE}, Nov 12 to Nov 16"}).json()
        r = client.post(f"/api/trips/{trip['id']}/messages", json={"text": "Dec 24 to Dec 22"}).json()
        f = _fields(r)
        assert f["depart_date"] == "2026-11-12" and f["return_date"] == "2026-11-16"
        assert r["brief"]["nights"] == 4


def test_already_inverted_brief_cannot_be_searched(sample_brief):
    """Trips saved before the fix can still hold inverted dates."""
    sample_brief["depart_date"]["value"] = "2026-12-24"
    assert "return date" in B.date_problem(sample_brief)


# ---------------------------------------------------------------- chat crashes


@pytest.mark.parametrize("text", ["Nov 31 to Dec 2", "Nov 12-31", "Feb 29 trip", "Feb 30", "2/30", "2/30 - 3/2"])
def test_impossible_dates_are_flagged_not_fatal(text):
    fields, signals = extract(text, B.with_defaults(B.empty_brief()), today=TODAY)
    assert "depart_date" not in fields and "return_date" not in fields
    assert signals["bad_date"]


@pytest.mark.parametrize("text", ["available 24/7", "50/50 on the airport", "13/40 - 14/50"])
def test_numbers_with_slashes_are_not_dates(text):
    fields, signals = extract(text, B.with_defaults(B.empty_brief()), today=TODAY)
    assert "depart_date" not in fields and "return_date" not in fields
    assert not signals.get("bad_date")


def test_feb_29_finds_the_next_leap_year():
    fields, _ = extract("Feb 29", B.with_defaults(B.empty_brief()), today=date(2027, 11, 1))
    assert fields["depart_date"] == "2028-02-29"


def test_huge_night_count_is_not_fatal():
    fields, signals = extract("Nov 12 for 99999999999 nights", B.with_defaults(B.empty_brief()), today=TODAY)
    assert "return_date" not in fields and signals["bad_date"]


@pytest.mark.parametrize("text", ["Nov 31 to Dec 2", "available 24/7", "50/50 on the airport", "Feb 29 trip", "Nov 12 for 99999999999 nights"])
def test_chat_survives_odd_dates(text):
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={}).json()
        assert client.post(f"/api/trips/{trip['id']}/messages", json={"text": text}).status_code == 200
        assert client.post("/api/trips", json={"message": text}).status_code == 200


def test_chat_says_when_a_date_is_impossible():
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={"message": f"{ROUTE}, Nov 12 to Nov 16"}).json()
        client.post(f"/api/trips/{trip['id']}/plans")
        r = client.post(f"/api/trips/{trip['id']}/messages", json={"text": "Nov 31"}).json()
        assert "calendar" in r["reply"]


def test_failed_first_message_leaves_no_orphan_trip(monkeypatch):
    def boom(*_):
        raise RuntimeError("assistant down")

    monkeypatch.setattr("app.main.respond", boom)
    with TestClient(app, raise_server_exceptions=False) as client:
        before = client.get("/api/admin/metrics", headers=ADMIN).json()["trips"]
        assert client.post("/api/trips", json={"message": "hello"}).status_code == 500
        assert client.get("/api/admin/metrics", headers=ADMIN).json()["trips"] == before


# ---------------------------------------------------------------- flights already gone


def test_departed_flights_are_not_offered(monkeypatch):
    moment = datetime(2026, 11, 12, 13, 0, tzinfo=ET)
    set_clock(monkeypatch, moment)
    result = search_plans(_brief())
    assert result["plans"]
    for plan in result["plans"]:
        assert datetime.fromisoformat(plan["flights"]["outbound"]["depart"]) > moment
        assert datetime.fromisoformat(plan["legs"][0]["start"]) > moment


def test_no_flights_left_today_is_explained(monkeypatch):
    set_clock(monkeypatch, datetime(2026, 11, 12, 23, 30, tzinfo=ET))
    result = search_plans(_brief(arrive_by="12:00"))
    assert result["plans"] == []
    assert len(result["notes"]) == 1 and "left" in result["notes"][0]


def test_past_trip_cannot_be_searched(monkeypatch):
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={"message": f"{ROUTE}, Nov 12 to Nov 16"}).json()
        set_clock(monkeypatch, datetime(2026, 11, 13, 12, 0, tzinfo=ET))
        r = client.post(f"/api/trips/{trip['id']}/plans")
        assert r.status_code == 422 and "past" in r.json()["detail"]["message"]
        r = client.post(f"/api/trips/{trip['id']}/messages", json={"text": "compare plans"}).json()
        assert "past" in r["reply"] and r["plans"] is None


def test_chat_reports_when_no_plans_are_found(monkeypatch):
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={"message": f"{ROUTE}, Nov 12 to Nov 16"}).json()
        set_clock(monkeypatch, datetime(2026, 11, 12, 23, 30, tzinfo=ET))
        r = client.post(f"/api/trips/{trip['id']}/messages", json={"text": "compare plans"}).json()
        assert "compared 0" not in r["reply"] and "left" in r["reply"]


# ---------------------------------------------------------------- transit hours


@pytest.mark.parametrize("origin", ["Washington Heights", "Fordham", "Flushing", "Park Slope", "Upper West Side"])
def test_transit_legs_start_within_service_hours(origin):
    transit = rates()["transit"]
    for offset in range(30):
        day = date(2026, 11, 2) + timedelta(days=offset)
        result = search_plans(_brief(origin=origin, priority="cheapest", carry_on=0, depart_date=day.isoformat(), return_date=(day + timedelta(days=3)).isoformat()))
        for plan in result["plans"]:
            out, back = plan["flights"]["outbound"], plan["flights"]["return"]
            for leg, code in zip(plan["legs"], (out["origin"], out["destination"], back["origin"], back["destination"])):
                if leg["mode"] == "transit":
                    opens, closes = (time.fromisoformat(s) for s in transit[code]["service"])
                    assert opens <= datetime.fromisoformat(leg["start"]).time() <= closes, (day, leg["label"], leg["start_label"])


# ---------------------------------------------------------------- driving


@pytest.mark.parametrize("priority", ["cheapest", "balanced", "easiest"])
def test_choosing_to_drive_means_every_plan_drives(priority):
    for plan in search_plans(_brief(will_drive=True, priority=priority))["plans"]:
        modes = [l["mode"] for l in plan["legs"]]
        assert modes[0] == "drive" and modes[3] == "drive"
        assert "drive" not in modes[1:3]
        assert sum(i["category"] == "parking" for i in plan["items"]) == 1


def test_not_driving_means_no_plan_drives():
    for plan in search_plans(_brief(will_drive=False))["plans"]:
        assert all(l["mode"] != "drive" for l in plan["legs"])
        assert not any(i["category"] == "parking" for i in plan["items"])


# ---------------------------------------------------------------- Hudson tolls


@pytest.mark.parametrize(
    "place, airport, to_airport, crosses",
    [
        ("upper_west_side", "EWR", True, False),  # leaving New York is free
        ("upper_west_side", "EWR", False, True),
        ("park_slope", "EWR", False, True),
        ("jersey_city", "EWR", True, False),
        ("hoboken", "EWR", False, False),
        ("jersey_city", "LGA", True, True),
        ("jersey_city", "LGA", False, False),
        ("hoboken", "JFK", True, True),
        ("upper_west_side", "LGA", True, False),
        ("astoria", "JFK", False, False),
    ],
)
def test_hudson_toll_applies_only_when_entering_new_york(place, airport, to_airport, crosses):
    when = datetime(2026, 11, 12, 11, 0, tzinfo=ET)
    routing = EstimateRouting()
    p = geo.get_place(place)
    ride = rideshare(p, airport, when, routing, to_airport=to_airport)
    drive = drive_self(p, airport, when, routing, to_airport=to_airport)
    toll = rates()["rideshare"]["NYC"]["hudson_toll"]
    assert any("toll" in n for n in ride.notes) is crosses

    miles = routing.drive(p.point, (geo.airport(airport)["lat"], geo.airport(airport)["lng"]), "NYC", when).miles
    assert drive.likely == round(miles * rates()["driving"]["cost_per_mile"] + (toll if crosses else 0))


# ---------------------------------------------------------------- pilot metrics


def _trip_with_plans(client):
    trip = client.post("/api/trips", json={"message": f"{ROUTE}, Nov 12 to Nov 16"}).json()
    return trip["id"], client.post(f"/api/trips/{trip['id']}/plans").json()


def _click(client, trip_id, search, plan_id="A", url=None):
    plan = next(p for p in search["plans"] if p["id"] == plan_id)
    url = url or plan["booking_links"][0]["url"]
    return client.get("/api/out", params={"url": url, "trip": trip_id, "plan": plan_id, "search": search["search_id"]}, follow_redirects=False)


def _clicked(client):
    return client.get("/api/admin/metrics", headers=ADMIN).json()["trips_clicked_to_book"]


def test_real_click_counts():
    with TestClient(app) as client:
        before = _clicked(client)
        trip_id, search = _trip_with_plans(client)
        assert _click(client, trip_id, search).status_code == 302
        assert _clicked(client) == before + 1


@pytest.mark.parametrize(
    "params",
    [
        {"trip": "made-up"},
        {},
        {"trip": "REAL"},
        {"trip": "REAL", "plan": "A"},
        {"trip": "REAL", "plan": "A", "search": "made-up"},
        {"trip": "REAL", "plan": "Z", "search": "REAL"},
        {"trip": "REAL", "plan": "A", "search": "OTHER"},
    ],
)
def test_unverifiable_click_redirects_but_is_not_counted(params):
    with TestClient(app) as client:
        trip_id, search = _trip_with_plans(client)
        _, other = _trip_with_plans(client)
        url = search["plans"][0]["booking_links"][0]["url"]
        swap = {"REAL": trip_id}
        values = {k: swap.get(v, v) for k, v in params.items()}
        if values.get("search") == "REAL":
            values["search"] = search["search_id"]
        if values.get("search") == "OTHER":
            values["search"] = other["search_id"]
        before = _clicked(client)
        r = client.get("/api/out", params={"url": url, **values}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == url
        assert _clicked(client) == before


def test_click_to_a_site_the_plan_does_not_use_is_not_counted():
    with TestClient(app) as client:
        trip_id, search = _trip_with_plans(client)
        plan = search["plans"][0]
        used = {l["url"] for l in plan["booking_links"]}
        other = next(u for u in ("https://www.aa.com", "https://www.delta.com", "https://www.jetblue.com") if u not in used)
        before = _clicked(client)
        assert _click(client, trip_id, search, url=other).status_code == 302
        assert _clicked(client) == before


def test_old_spoofed_clicks_cannot_push_the_rate_over_100_percent():
    with TestClient(app) as client:
        _trip_with_plans(client)
        with SessionLocal() as session:
            for i in range(50):
                log_event(session, "click_to_book", f"spoof{i}")
            session.commit()
        assert client.get("/api/admin/metrics", headers=ADMIN).json()["click_to_book_rate"] <= 1


@pytest.mark.parametrize("name", ["click_to_book", "llm_usage", "plans_searched", "anything"])
def test_browser_cannot_send_server_only_events(name):
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        before = client.get("/api/admin/metrics", headers=ADMIN).json()["events"].get(name, 0)
        assert client.post("/api/events", json={"name": name, "trip_id": trip_id, "props": {"input_tokens": 10**9}}).status_code == 422
        assert client.get("/api/admin/metrics", headers=ADMIN).json()["events"].get(name, 0) == before


@pytest.mark.parametrize("name", ["plans_viewed", "plan_explored"])
def test_browser_events_still_work(name):
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        assert client.post("/api/events", json={"name": name, "trip_id": trip_id, "props": {"plan": "A"}}).status_code == 200


def test_event_needs_a_real_trip():
    with TestClient(app) as client:
        assert client.post("/api/events", json={"name": "plans_viewed", "trip_id": "made-up"}).status_code == 404


@pytest.mark.parametrize("key", ["name", "session", "trip_id"])
def test_event_props_are_limited_to_known_fields(key):
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        assert client.post("/api/events", json={"name": "plans_viewed", "trip_id": trip_id, "props": {key: "x"}}).status_code == 422


# ---------------------------------------------------------------- spend reports


def _two_searches(client):
    """A trip whose plans were re-priced after the traveler followed one, so 'Plan A' means two different things."""
    trip_id, first = _trip_with_plans(client)
    client.patch(f"/api/trips/{trip_id}/brief", json={"checked_bags": 2, "priority": "easiest"})
    second = client.post(f"/api/trips/{trip_id}/plans").json()
    assert first["plans"][0]["totals"]["likely"] != second["plans"][0]["totals"]["likely"]
    return trip_id, first, second


def test_spend_compares_with_the_search_the_traveler_followed():
    with TestClient(app) as client:
        trip_id, first, _ = _two_searches(client)
        r = client.post(f"/api/trips/{trip_id}/spend", json={"plan_id": "A", "search_id": first["search_id"], "actual": {"flights": 300}}).json()
        assert r["estimated_total"] == first["plans"][0]["totals"]["likely"]
        assert r["difference"] == round(300 - first["plans"][0]["totals"]["likely"], 2)


def test_spend_stores_which_search_it_was_compared_with():
    with TestClient(app) as client:
        trip_id, first, _ = _two_searches(client)
        client.post(f"/api/trips/{trip_id}/spend", json={"plan_id": "A", "search_id": first["search_id"], "actual": {"flights": 300}})
        with SessionLocal() as session:
            row = session.scalar(select(SpendReport).where(SpendReport.trip_id == trip_id))
            assert row.search_id == first["search_id"]


def test_spend_on_a_plan_needs_its_search():
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        assert client.post(f"/api/trips/{trip_id}/spend", json={"plan_id": "A", "actual": {"flights": 300}}).status_code == 422


def test_spend_rejects_another_trips_search_or_unknown_plan():
    with TestClient(app) as client:
        trip_id, search = _trip_with_plans(client)
        _, other = _trip_with_plans(client)
        url = f"/api/trips/{trip_id}/spend"
        assert client.post(url, json={"plan_id": "A", "search_id": other["search_id"], "actual": {"flights": 1}}).status_code == 422
        assert client.post(url, json={"plan_id": "Z", "search_id": search["search_id"], "actual": {"flights": 1}}).status_code == 422


def test_spend_on_no_plan_has_no_estimate():
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        r = client.post(f"/api/trips/{trip_id}/spend", json={"plan_id": None, "actual": {"flights": 300}}).json()
        assert r["estimated_total"] is None and r["difference"] is None


def test_older_reports_with_no_known_search_are_left_out_of_the_average():
    with TestClient(app) as client:
        trip_id, search = _trip_with_plans(client)
        with SessionLocal() as session:
            session.query(SpendReport).delete()
            session.add(SpendReport(trip_id=trip_id, plan_id="A", estimated_total=100.0, actual={"flights": 5000.0}))
            session.commit()
        assert client.get("/api/admin/metrics", headers=ADMIN).json()["avg_actual_minus_estimate"] is None
        likely = search["plans"][0]["totals"]["likely"]
        client.post(f"/api/trips/{trip_id}/spend", json={"plan_id": "A", "search_id": search["search_id"], "actual": {"flights": likely + 10}})
        assert client.get("/api/admin/metrics", headers=ADMIN).json()["avg_actual_minus_estimate"] == 10


def test_state_reports_the_plan_the_traveler_clicked_through():
    with TestClient(app) as client:
        trip_id, first, _ = _two_searches(client)
        assert client.get(f"/api/trips/{trip_id}").json()["followed_plan"] is None
        _click(client, trip_id, first)
        followed = client.get(f"/api/trips/{trip_id}").json()["followed_plan"]
        plan = first["plans"][0]
        assert followed == {"search_id": first["search_id"], "plan_id": plan["id"], "title": plan["title"], "likely": plan["totals"]["likely"]}


def test_existing_database_gets_the_new_column(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE spend_reports (id INTEGER PRIMARY KEY, trip_id VARCHAR(32), plan_id VARCHAR(8), estimated_total FLOAT, actual JSON, note TEXT, created_at DATETIME)"))
        conn.execute(text("INSERT INTO spend_reports (trip_id, actual) VALUES ('t', '{}')"))
    for _ in range(2):  # safe to run on every startup
        add_missing_columns(engine)
    assert "search_id" in {c["name"] for c in inspect(engine).get_columns("spend_reports")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT trip_id, search_id FROM spend_reports")).one() == ("t", None)


# ---------------------------------------------------------------- input validation


@pytest.mark.parametrize(
    "actual",
    [
        '{"flights": -5000}',
        "{}",
        '{"flights": 0}',
        '{"flights": 1e999}',
        '{"flights": 1000000}',
        '{"made_up": 300}',
    ],
)
def test_bad_spend_amounts_are_rejected(actual):
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        before = client.get("/api/admin/metrics", headers=ADMIN).json()["spend_reports"]
        r = client.post(f"/api/trips/{trip_id}/spend", content=f'{{"actual": {actual}}}', headers={"content-type": "application/json"})
        assert r.status_code == 422
        assert client.get("/api/admin/metrics", headers=ADMIN).json()["spend_reports"] == before


def test_all_spend_categories_are_accepted():
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        r = client.post(f"/api/trips/{trip_id}/spend", json={"actual": {"flights": 300, "bags": 70, "rides": 90.5, "other": 0}})
        assert r.status_code == 200 and r.json()["actual_total"] == 460.5


@pytest.mark.parametrize(
    "body",
    [
        '{"observed_price": -5}',
        '{"observed_price": 1e999}',
        '{"shown_price": -1}',
        '{"plan_id": "way-too-long-for-the-column"}',
        '{"item_label": "' + "x" * 201 + '"}',
    ],
)
def test_bad_price_reports_are_rejected(body):
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        r = client.post(f"/api/trips/{trip_id}/price-reports", content=body, headers={"content-type": "application/json"})
        assert r.status_code == 422


@pytest.mark.parametrize("value", ["-500", "1e999"])
def test_budget_and_bags_reject_impossible_numbers(value):
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        for field in ("budget", "checked_bags"):
            r = client.patch(f"/api/trips/{trip_id}/brief", content=f'{{"{field}": {value}}}', headers={"content-type": "application/json"})
            assert r.status_code == 200 and r.json()["errors"]
            assert _fields(r.json())[field] in (None, 0)


def test_whitespace_message_is_rejected():
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        assert client.post(f"/api/trips/{trip_id}/messages", json={"text": "   "}).status_code == 422


def test_malformed_outbound_url_is_a_clean_rejection():
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/out", params={"url": "https://[bad"}, follow_redirects=False).status_code == 400


# ---------------------------------------------------------------- assistant misreads


def _say(client, trip_id, text):
    return client.post(f"/api/trips/{trip_id}/messages", json={"text": text}).json()


@pytest.mark.parametrize(
    "text",
    [
        "From Wicker Park to Harlem",
        "flying from Chicago to New York",
        "Chicago to NYC",
        "Wicker Park to Harlem",
        "Flying from the Loop",
        "Going to Harlem from Evanston",
        "Flying to Harlem",
    ],
)
def test_reversed_trips_are_flagged_not_flipped(text):
    fields, signals = extract(text, B.with_defaults(B.empty_brief()), today=TODAY)
    assert signals["reversed"]
    assert "origin" not in fields and "destination" not in fields


@pytest.mark.parametrize(
    "text, origin, destination",
    [
        ("From Harlem to Wicker Park", "harlem", "wicker_park"),
        ("Wicker Park from Harlem", "harlem", "wicker_park"),
        ("Going to Wicker Park from Harlem", "harlem", "wicker_park"),
        ("Harlem to Wicker Park, flying back from Chicago Dec 8", "harlem", "wicker_park"),
        ("Harlem to Wicker Park and back to Harlem", "harlem", "wicker_park"),
        ("Flying home from Wicker Park to Harlem after a trip from Harlem", "harlem", "wicker_park"),
    ],
)
def test_normal_trips_are_not_flagged_as_reversed(text, origin, destination):
    fields, signals = extract(text, B.with_defaults(B.empty_brief()), today=TODAY)
    assert not signals.get("reversed")
    assert fields["origin"] == origin and fields["destination"] == destination


def test_chat_explains_it_cannot_plan_a_reversed_trip():
    with TestClient(app) as client:
        trip_id = client.post("/api/trips", json={}).json()["id"]
        r = _say(client, trip_id, "From Wicker Park to Harlem")
        f = _fields(r)
        assert f["origin"] is None and f["destination"] is None
        assert "starting in the New York City area" in r["reply"]


@pytest.mark.parametrize(
    "text",
    ["Why is the cheapest plan not recommended?", "Is the easiest plan worth it?", "What's the lowest total?", "Is LaGuardia easier from Chicago?", "Plan A looks cheap?"],
)
def test_questions_about_plans_do_not_change_the_brief(text):
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        before = client.get(f"/api/trips/{trip_id}").json()
        r = _say(client, trip_id, text)
        assert r["brief_version"] == before["brief_version"] and r["plans"]["is_current"]
        assert "$" in r["reply"]


def test_asking_for_a_change_after_plans_still_changes_the_brief():
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        assert _fields(_say(client, trip_id, "I want the cheapest trip"))["priority"] == "cheapest"
        assert _fields(_say(client, trip_id, "Can we do Nov 20 to Nov 24 instead?"))["depart_date"] == "2026-11-20"
        assert _fields(_say(client, trip_id, "Could I land by 6pm?"))["arrive_by"] == "18:00"


def test_can_you_compare_again_runs_a_new_search():
    with TestClient(app) as client:
        trip_id, first = _trip_with_plans(client)
        r = _say(client, trip_id, "Can you compare plans again?")
        assert r["plans_searched"] and r["plans"]["search_id"] != first["search_id"]


def test_a_question_before_any_plans_keeps_trip_details_but_not_a_priority():
    fields, _ = extract("Harlem to Wicker Park", B.with_defaults(B.empty_brief()), today=TODAY)
    assert fields
    with TestClient(app) as client:
        trip_id = client.post("/api/trips", json={}).json()["id"]
        f = _fields(_say(client, trip_id, "What's the cheapest way from Harlem to Wicker Park, Nov 12 to Nov 16?"))
        assert f["origin"] == "harlem" and f["depart_date"] == "2026-11-12"
        assert f["priority"] == "balanced"


@pytest.mark.parametrize("chip", ["I'd rather spend less", "I want the cheapest", "something cheaper please"])
def test_spend_less_phrases_set_the_priority(chip):
    fields, _ = extract(chip, B.with_defaults(B.empty_brief()), today=TODAY)
    assert fields["priority"] == "cheapest"


@pytest.mark.parametrize(
    "text",
    ["up to 2 checked bags", "no more than 1 checked bag", "max 2 bags", "spend 3 nights", "under 5 hours", "up to 2 stops", "no more than 2 transfers"],
)
def test_counts_are_not_budgets(text):
    fields, _ = extract(text, B.with_defaults(B.empty_brief()), today=TODAY)
    assert "budget" not in fields


@pytest.mark.parametrize(
    "text, amount",
    [("under $900", 900), ("under 900", 900), ("budget of 1,500", 1500), ("up to 2k", 2000), ("no more than $750", 750), ("spend 600", 600), ("$500", 500)],
)
def test_real_budgets_still_read(text, amount):
    fields, _ = extract(text, B.with_defaults(B.empty_brief()), today=TODAY)
    assert fields["budget"] == amount


def test_up_to_two_checked_bags_keeps_the_bag_count():
    fields, _ = extract("up to 2 checked bags", B.with_defaults(B.empty_brief()), today=TODAY)
    assert fields["checked_bags"] == 2


def test_nights_said_earlier_set_the_return_date():
    with TestClient(app) as client:
        trip_id = client.post("/api/trips", json={}).json()["id"]
        assert "count the nights" in _say(client, trip_id, "Harlem to Wicker Park for 4 nights")["reply"]
        f = _fields(_say(client, trip_id, "Nov 12"))
        assert f["depart_date"] == "2026-11-12" and f["return_date"] == "2026-11-16"


def test_earlier_nights_do_not_override_a_stated_return():
    with TestClient(app) as client:
        trip_id = client.post("/api/trips", json={}).json()["id"]
        _say(client, trip_id, "Harlem to Wicker Park for 4 nights")
        f = _fields(_say(client, trip_id, "Nov 12 to Nov 14"))
        assert f["return_date"] == "2026-11-14"
        f = _fields(_say(client, trip_id, "leaving Nov 11"))
        assert f["depart_date"] == "2026-11-11" and f["return_date"] == "2026-11-14"


@pytest.mark.parametrize(
    "curly, expected",
    [
        ("I’ll drive", {"will_drive": True}),
        ("I don’t have a car", {"will_drive": False}),
        ("I don‘t drive", {"will_drive": False}),
        ("Hell’s Kitchen to Wicker Park", {"origin": "midtown", "destination": "wicker_park"}),
    ],
)
def test_curly_apostrophes_read_like_straight_ones(curly, expected):
    fields, _ = extract(curly, B.with_defaults(B.empty_brief()), today=TODAY)
    assert fields == expected


def test_curly_lets_go_starts_a_search():
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={"message": f"{ROUTE}, Nov 12 to Nov 16"}).json()
        assert _say(client, trip["id"], "let’s go")["plans_searched"]


def test_curly_place_works_in_brief_edits():
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={}).json()
        r = client.patch(f"/api/trips/{trip['id']}/brief", json={"origin": "Hell’s Kitchen"}).json()
        assert _fields(r)["origin"] == "midtown"


# ---------------------------------------------------------------- moving a trip


def test_moving_only_the_departure_later_moves_the_whole_trip(sample_brief):
    b, notices = B.apply_updates(sample_brief, {"depart_date": "2026-12-20"}, "user_said")
    assert b["depart_date"]["value"] == "2026-12-20" and b["return_date"]["value"] == "2026-12-24"
    assert b["depart_date"]["provenance"] == "user_said" and b["return_date"]["provenance"] == "assumed"
    assert "4-night trip" in notices[0]


def test_moving_the_departure_earlier_leaves_the_return_alone(sample_brief):
    b, notices = B.apply_updates(sample_brief, {"depart_date": "2026-11-10"}, "user_said")
    assert b["return_date"]["value"] == "2026-11-16" and not notices


def test_setting_only_the_return_before_departure_is_still_rejected(sample_brief):
    b, notices = B.apply_updates(sample_brief, {"return_date": "2026-11-01"}, "user_said")
    assert b["return_date"]["value"] == "2026-11-16" and "after the departure" in notices[0]


def test_a_moved_return_too_far_ahead_is_rejected_with_both_dates_kept(sample_brief):
    last = (TODAY + timedelta(days=B.MAX_DAYS_AHEAD)).isoformat()
    b, notices = B.apply_updates(sample_brief, {"depart_date": last}, "user_said")
    assert b["depart_date"]["value"] == "2026-11-12" and b["return_date"]["value"] == "2026-11-16"
    assert "after the departure" in notices[0]


def test_moving_the_trip_later_works_in_the_brief_and_marks_plans_stale():
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        r = client.patch(f"/api/trips/{trip_id}/brief", json={"depart_date": "2026-12-20"}).json()
        assert _fields(r)["return_date"] == "2026-12-24"
        assert "moved your return" in r["errors"][0] and not r["plans"]["is_current"]
