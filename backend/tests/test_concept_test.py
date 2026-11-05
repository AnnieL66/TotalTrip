"""Concept test: which plan each tester would book, and whether the door-to-door cost changes their choice."""
import csv
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from app import admin, concept
from app.config import settings
from app.db import Base, Event, SessionLocal, Trip
from app.main import app

from .conftest import ADMIN


def _code():
    code = uuid.uuid4().hex[:12]
    settings.tester_codes.add(code)
    return code


def _trip_with_plans(client, tester=None, demo="newark_trap"):
    trip_id = client.post(f"/api/trips/demo/{demo}", json={"tester": tester}).json()["id"]
    return trip_id, client.post(f"/api/trips/{trip_id}/plans").json()


def _concept(client):
    return client.get("/api/admin/metrics", headers=ADMIN).json()["concept_test"]


def _plan_id(search, tag):
    return next(p["id"] for p in search["plans"] if tag in p["tags"])


def _choose(client, trip_id, search, tag=None):
    body = {"search_id": search["search_id"], "plan_id": _plan_id(search, tag) if tag else None}
    assert client.post(f"/api/trips/{trip_id}/plan-choice", json=body).status_code == 200


def _rows(tester):
    with SessionLocal() as session:
        return [r for r in concept.trip_rows(session) if r["tester"] == tester]


def test_choices_are_counted_by_kind():
    with TestClient(app) as client:
        before = _concept(client)
        for tag in ("Best fit", "Lowest booking price", None):
            trip_id, search = _trip_with_plans(client, _code())
            _choose(client, trip_id, search, tag)
        after = _concept(client)
        assert after["testers"] == before["testers"] + 3
        assert after["chose_recommended"] == before["chose_recommended"] + 1
        assert after["chose_cheapest_flights"] == before["chose_cheapest_flights"] + 1
        assert after["chose_none"] == before["chose_none"] + 1


def test_changing_your_mind_counts_once():
    with TestClient(app) as client:
        before = _concept(client)
        trip_id, search = _trip_with_plans(client, _code())
        _choose(client, trip_id, search, "Lowest booking price")
        _choose(client, trip_id, search, "Best fit")
        after = _concept(client)
        assert after["testers"] == before["testers"] + 1
        assert after["chose_recommended"] == before["chose_recommended"] + 1
        assert after["chose_cheapest_flights"] == before["chose_cheapest_flights"]


def test_a_tester_with_several_trips_counts_once_but_every_trip_shows_in_by_trip():
    with TestClient(app) as client:
        before = _concept(client)
        code = _code()
        for demo in ("newark_trap", "tight_budget"):
            trip_id, search = _trip_with_plans(client, code, demo)
            _choose(client, trip_id, search, "Best fit")
        after = _concept(client)
        assert after["testers"] == before["testers"] + 1
        assert after["chose_recommended"] == before["chose_recommended"] + 1
        for demo in ("newark_trap", "tight_budget"):
            was = before["by_trip"].get(demo, {}).get("chose_recommended", 0)
            assert after["by_trip"][demo]["chose_recommended"] == was + 1


def test_trips_without_a_code_are_left_out_but_reported():
    with TestClient(app) as client:
        before = _concept(client)
        trip_id, search = _trip_with_plans(client)
        _choose(client, trip_id, search, "Best fit")
        after = _concept(client)
        assert after["testers"] == before["testers"]
        assert after["chose_recommended"] == before["chose_recommended"]
        assert after["untagged_trips_left_out"] == before["untagged_trips_left_out"] + 1


def test_typed_trips_keep_their_tester_code():
    with TestClient(app) as client:
        code = _code()
        trip = client.post("/api/trips", json={"message": "Harlem to Wicker Park, Nov 12 to Nov 16", "tester": code}).json()
        search = client.post(f"/api/trips/{trip['id']}/plans").json()
        _choose(client, trip["id"], search, "Best fit")
        assert [(r["tester"], r["trip"]) for r in _rows(code)] == [(code, "typed")]


@pytest.mark.parametrize("code", ["has space", "-leading", "x" * 33, "<script>", ""])
def test_bad_tester_codes_are_rejected(code):
    with TestClient(app) as client:
        assert client.post("/api/trips", json={"tester": code}).status_code == 422
        assert client.post("/api/trips/demo/newark_trap", json={"tester": code}).status_code == 422


def test_answers_are_counted_and_notes_stay_out_of_metrics():
    with TestClient(app) as client:
        before = _concept(client)["would_change"]
        code = _code()
        trip_id, search = _trip_with_plans(client, code)
        body = {"search_id": search["search_id"], "plan_id": "A", "would_change": "maybe", "note": "secret-note-text"}
        assert client.post(f"/api/trips/{trip_id}/concept-answer", json=body).status_code == 200
        client.post(f"/api/trips/{trip_id}/concept-answer", json={**body, "would_change": "yes"})
        after = _concept(client)["would_change"]
        assert after["yes"] == before["yes"] + 1 and after["maybe"] == before["maybe"]
        assert "secret-note-text" not in client.get("/api/admin/metrics", headers=ADMIN).text
        assert _rows(code)[0]["note"] == "secret-note-text"


def _answer(client, trip_id, search, plan_id, would_change="yes", note=None):
    body = {"search_id": search["search_id"], "plan_id": plan_id, "would_change": would_change, "note": note}
    assert client.post(f"/api/trips/{trip_id}/concept-answer", json=body).status_code == 200


def test_answer_about_one_plan_is_not_credited_to_a_later_choice():
    with TestClient(app) as client:
        code = _code()
        trip_id, search = _trip_with_plans(client, code)
        first, second = _plan_id(search, "Best fit"), _plan_id(search, "Lowest booking price")
        client.post(f"/api/trips/{trip_id}/plan-choice", json={"search_id": search["search_id"], "plan_id": first})
        _answer(client, trip_id, search, first, note="about the first plan")
        client.post(f"/api/trips/{trip_id}/plan-choice", json={"search_id": search["search_id"], "plan_id": second})
        row = _rows(code)[0]
        assert row["plan"] == second
        assert row["would_change"] == "" and row["note"] == "" and row["answered_at"] == ""


def test_answer_from_an_older_search_is_not_credited_to_a_new_one():
    with TestClient(app) as client:
        code = _code()
        trip_id, old = _trip_with_plans(client, code)
        _answer(client, trip_id, old, "A")
        new = client.post(f"/api/trips/{trip_id}/plans?recheck=true").json()
        client.post(f"/api/trips/{trip_id}/plan-choice", json={"search_id": new["search_id"], "plan_id": "A"})
        row = _rows(code)[0]
        assert row["search_id"] == new["search_id"] and row["would_change"] == ""


def test_going_back_to_a_plan_keeps_the_answer_about_it():
    with TestClient(app) as client:
        code = _code()
        trip_id, search = _trip_with_plans(client, code)
        first, second = _plan_id(search, "Best fit"), _plan_id(search, "Lowest booking price")
        for plan in (first, second, first):
            client.post(f"/api/trips/{trip_id}/plan-choice", json={"search_id": search["search_id"], "plan_id": plan})
            if plan == first:
                _answer(client, trip_id, search, first, "maybe")
        assert _rows(code)[0]["would_change"] == "maybe"


def test_answer_counts_even_if_saving_its_choice_failed():
    """The choice is saved separately, so it can be missing or older. The answer says which plan it's about."""
    with TestClient(app) as client:
        code = _code()
        trip_id, search = _trip_with_plans(client, code)
        first, second = _plan_id(search, "Best fit"), _plan_id(search, "Lowest booking price")
        client.post(f"/api/trips/{trip_id}/plan-choice", json={"search_id": search["search_id"], "plan_id": first})
        _answer(client, trip_id, search, second, "no")
        row = _rows(code)[0]
        assert row["plan"] == second and row["would_change"] == "no" and row["chosen_at"] == ""


def test_tester_codes_we_did_not_hand_out_do_not_count():
    with TestClient(app) as client:
        before = _concept(client)
        trip_id, search = _trip_with_plans(client, "made-up-code")
        _choose(client, trip_id, search, "Best fit")
        after = _concept(client)
        assert after["testers"] == before["testers"]
        assert after["untagged_trips_left_out"] == before["untagged_trips_left_out"] + 1


@pytest.mark.parametrize("path", ["plan-choice", "concept-answer"])
def test_choice_must_belong_to_the_trip(path):
    with TestClient(app) as client:
        trip_id, search = _trip_with_plans(client)
        _, other = _trip_with_plans(client)
        extra = {"would_change": "yes"} if path == "concept-answer" else {}
        url = f"/api/trips/{trip_id}/{path}"
        assert client.post(url, json={"search_id": other["search_id"], "plan_id": "A", **extra}).status_code == 422
        assert client.post(url, json={"search_id": "made-up", "plan_id": "A", **extra}).status_code == 422
        assert client.post(url, json={"search_id": search["search_id"], "plan_id": "Z", **extra}).status_code == 422
        assert client.post("/api/trips/made-up/" + path, json={"search_id": search["search_id"], **extra}).status_code == 404


@pytest.mark.parametrize("body", [{"would_change": "definitely"}, {}, {"would_change": "yes", "note": "x" * 2001}])
def test_bad_answers_are_rejected(body):
    with TestClient(app) as client:
        trip_id, search = _trip_with_plans(client)
        r = client.post(f"/api/trips/{trip_id}/concept-answer", json={"search_id": search["search_id"], **body})
        assert r.status_code == 422


def test_browser_cannot_send_choices_as_plain_events():
    with TestClient(app) as client:
        trip_id, _ = _trip_with_plans(client)
        r = client.post("/api/events", json={"name": "plan_chosen", "trip_id": trip_id, "props": {"plan": "A"}})
        assert r.status_code == 422


# ---------------------------------------------------------------- export and reset


def test_export_has_each_trips_choice_and_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "init_db", lambda: None)
    with TestClient(app) as client:
        code = _code()
        trip_id, search = _trip_with_plans(client, code)
        plan_id = _plan_id(search, "Best fit")
        _choose(client, trip_id, search, "Best fit")
        client.post(f"/api/trips/{trip_id}/concept-answer", json={"search_id": search["search_id"], "plan_id": plan_id, "would_change": "yes", "note": "Great, thanks"})
    path = tmp_path / "results.csv"
    assert admin.main(["export", str(path)]) == 0
    with open(path, newline="", encoding="utf-8") as f:
        row = next(r for r in csv.DictReader(f) if r["tester"] == code)
    plan = next(p for p in search["plans"] if p["id"] == plan_id)
    assert row["trip"] == "newark_trap" and row["plan"] == plan_id and row["plan_title"] == plan["title"]
    assert float(row["plan_total"]) == plan["totals"]["likely"]
    assert row["recommended"] == "True" and row["would_change"] == "yes" and row["note"] == "Great, thanks"


@pytest.mark.parametrize("note", ["=1+1", "+1", "-1", "@SUM(A1)", "  =HYPERLINK(1)"])
def test_export_stops_notes_running_as_spreadsheet_formulas(tmp_path, note):
    with TestClient(app) as client:
        code = _code()
        trip_id, search = _trip_with_plans(client, code)
        client.post(f"/api/trips/{trip_id}/concept-answer", json={"search_id": search["search_id"], "plan_id": "A", "would_change": "no", "note": note})
    path = tmp_path / "results.csv"
    with SessionLocal() as session, open(path, "w", newline="", encoding="utf-8") as out:
        admin.write_csv(session, out)
    with open(path, newline="", encoding="utf-8") as f:
        row = next(r for r in csv.DictReader(f) if r["tester"] == code)
    assert row["note"] == f"'{note}"


def _throwaway_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'reset.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Trip(id="t1"))
        session.add(Event(trip_id="t1", name="trip_created", props={}))
        session.commit()
    return engine


def _count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_reset_deletes_rows_and_keeps_tables(tmp_path):
    engine = _throwaway_engine(tmp_path)
    admin.delete_everything(engine)
    assert _count(engine, Trip) == 0 and _count(engine, Event) == 0
    assert "trips" in inspect(engine).get_table_names()


@pytest.mark.parametrize("typed", ["", "yes", "RESET"])
def test_reset_needs_the_word_typed_exactly(tmp_path, monkeypatch, typed):
    engine = _throwaway_engine(tmp_path)
    monkeypatch.setattr(admin, "engine", engine)
    monkeypatch.setattr(admin, "init_db", lambda: None)
    monkeypatch.setattr("builtins.input", lambda _: typed)
    assert admin.main(["reset"]) == 1
    assert _count(engine, Trip) == 1


def test_reset_after_confirmation(tmp_path, monkeypatch):
    engine = _throwaway_engine(tmp_path)
    monkeypatch.setattr(admin, "engine", engine)
    monkeypatch.setattr(admin, "init_db", lambda: None)
    monkeypatch.setattr("builtins.input", lambda _: "reset")
    assert admin.main(["reset"]) == 0
    assert _count(engine, Trip) == 0
