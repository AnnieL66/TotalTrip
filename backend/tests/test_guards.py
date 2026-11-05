"""What protects a public deployment: admin access, request limits, the LLM budget, and no transaction held through an LLM call."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from app import brief as B
from app import limits
from app.ai import agent as agent_module
from app.ai.tools import ToolBox
from app.config import settings
from app.db import Event, SessionLocal, Trip, log_event
from app.main import app
from app.trips import current_brief, save_brief

from .conftest import ADMIN

# ---------------------------------------------------------------- admin


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "test-admin-token-x"}, {"Authorization": "Bearer "}, {"Authorization": "Bearer tést"}])
def test_metrics_need_the_admin_token(headers):
    with TestClient(app) as client:
        assert client.get("/api/admin/metrics", headers={k: v.encode() for k, v in headers.items()}).status_code == 401


def test_metrics_open_with_the_admin_token():
    with TestClient(app) as client:
        assert client.get("/api/admin/metrics", headers=ADMIN).status_code == 200


@pytest.mark.parametrize("token", [None, ""])
def test_no_admin_token_set_means_nobody_gets_in(monkeypatch, token):
    monkeypatch.setattr(settings, "admin_token", token)
    with TestClient(app) as client:
        assert client.get("/api/admin/metrics", headers={"Authorization": "Bearer "}).status_code == 401
        assert client.get("/api/admin/metrics").status_code == 401


# ---------------------------------------------------------------- rate limits


def test_too_many_trips_from_one_visitor(monkeypatch):
    monkeypatch.setattr(settings, "trips_per_hour_per_ip", 2)
    with TestClient(app) as client:
        assert client.post("/api/trips", json={}).status_code == 200
        assert client.post("/api/trips/demo/newark_trap").status_code == 200
        assert client.post("/api/trips", json={}).status_code == 429


def test_too_many_messages_from_one_visitor_even_across_trips(monkeypatch):
    monkeypatch.setattr(settings, "messages_per_hour_per_ip", 2)
    with TestClient(app) as client:
        a, b = client.post("/api/trips", json={}).json()["id"], client.post("/api/trips", json={}).json()["id"]
        assert client.post(f"/api/trips/{a}/messages", json={"text": "hi"}).status_code == 200
        assert client.post(f"/api/trips/{b}/messages", json={"text": "hi"}).status_code == 200
        assert client.post(f"/api/trips/{b}/messages", json={"text": "hi"}).status_code == 429


def test_new_trips_do_not_reset_the_search_limit(monkeypatch):
    monkeypatch.setattr(settings, "searches_per_hour_per_ip", 1)
    with TestClient(app) as client:
        a = client.post("/api/trips/demo/newark_trap").json()["id"]
        b = client.post("/api/trips/demo/newark_trap").json()["id"]
        assert client.post(f"/api/trips/{a}/plans").status_code == 200
        assert client.post(f"/api/trips/{b}/plans").status_code == 429


def test_visitors_behind_a_load_balancer_have_separate_limits(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
    monkeypatch.setattr(settings, "trips_per_hour_per_ip", 1)
    with TestClient(app) as client:
        assert client.post("/api/trips", json={}, headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 200
        assert client.post("/api/trips", json={}, headers={"X-Forwarded-For": "203.0.113.2"}).status_code == 200
        assert client.post("/api/trips", json={}, headers={"X-Forwarded-For": "203.0.113.2"}).status_code == 429


def test_a_visitor_cannot_escape_the_limit_by_faking_the_forwarded_header(monkeypatch):
    """The load balancer appends the address it saw, so anything earlier in the list is whatever the visitor sent."""
    monkeypatch.setattr(settings, "trusted_proxy_hops", 1)
    monkeypatch.setattr(settings, "trips_per_hour_per_ip", 1)
    with TestClient(app) as client:
        assert client.post("/api/trips", json={}, headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.9"}).status_code == 200
        assert client.post("/api/trips", json={}, headers={"X-Forwarded-For": "2.2.2.2, 203.0.113.9"}).status_code == 429


def test_forwarded_header_is_ignored_by_default(monkeypatch):
    """Next.js passes on whatever the visitor sent, so the header means nothing until a load balancer is configured."""
    monkeypatch.setattr(settings, "trips_per_hour_per_ip", 1)
    with TestClient(app) as client:
        assert client.post("/api/trips", json={}, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
        assert client.post("/api/trips", json={}, headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


# ---------------------------------------------------------------- request size and shape


def test_oversized_bodies_are_refused():
    with TestClient(app) as client:
        trip_id = client.post("/api/trips", json={}).json()["id"]
        r = client.post("/api/events", json={"trip_id": trip_id, "name": "plans_viewed", "props": {"x": "a" * 100_000}})
        assert r.status_code == 413


def test_oversized_bodies_without_a_declared_length_are_refused():
    def chunks():
        yield b'{"message": "'
        yield b"a" * (settings.max_request_bytes + 1)
        yield b'"}'

    with TestClient(app) as client:
        assert client.post("/api/trips", content=chunks()).status_code == 413


def test_normal_bodies_at_the_field_limits_still_fit():
    with TestClient(app) as client:
        assert client.post("/api/trips", json={"message": "é" * 2000}).status_code == 200


@pytest.mark.parametrize("props", [{"x": "y"}, {"search_id": "a" * 33}, {"plan": "a" * 9}, {"plans": 10_000}])
def test_event_props_are_limited(props):
    with TestClient(app) as client:
        trip_id = client.post("/api/trips", json={}).json()["id"]
        assert client.post("/api/events", json={"trip_id": trip_id, "name": "plans_viewed", "props": props}).status_code == 422


def test_unknown_brief_fields_are_rejected_without_saving():
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={}).json()
        r = client.patch(f"/api/trips/{trip['id']}/brief", json={"budget": 500, "made_up": 1})
        assert r.status_code == 422
        assert client.get(f"/api/trips/{trip['id']}").json()["brief_version"] == trip["brief_version"]


# ---------------------------------------------------------------- the LLM


class _FakeResponses:
    """Stands in for OpenAI: asks for one tool call, then replies. Checks the database is free during each call."""

    def __init__(self, box, tokens=10):
        self.box, self.tokens, self.calls = box, tokens, 0

    def create(self, **_):
        self.calls += 1
        assert not self.box.session.in_transaction(), "transaction held through an LLM call"
        with SessionLocal() as other:  # another request has to be able to write right now
            log_event(other, "other_request", None)
            other.commit()
        usage = SimpleNamespace(input_tokens=self.tokens, output_tokens=self.tokens)
        if self.calls == 1:
            call = SimpleNamespace(type="function_call", name="update_brief", call_id="c1", arguments='{"stated": {"budget": 700}}')
            return SimpleNamespace(usage=usage, output=[call], output_text="")
        return SimpleNamespace(usage=usage, output=[], output_text="All set.")


def _openai_agent(box, **kw):
    agent = agent_module.OpenAIAgent.__new__(agent_module.OpenAIAgent)
    agent.client = SimpleNamespace(responses=_FakeResponses(box, **kw))
    return agent


def _box(session):
    trip = Trip()
    session.add(trip)
    session.flush()

    save_brief(session, trip, B.with_defaults(B.empty_brief()), "assistant")
    return ToolBox(session, trip)


def test_no_transaction_is_held_through_an_llm_call():
    with SessionLocal() as session:
        box = _box(session)
        out = _openai_agent(box).respond(box, [], "my budget is 700")
        assert out["reply"] == "All set."
        assert box.brief_changed
        session.rollback()  # what the assistant saved doesn't wait for the end of the turn

        assert current_brief(session, box.trip)["budget"]["value"] == 700


def test_a_failed_tool_is_rolled_back_and_the_turn_continues(monkeypatch):
    with SessionLocal() as session:
        box = _box(session)

        def boom(**_):
            session.execute(insert(Event).values(id=1, name="x", props={}))
            session.execute(insert(Event).values(id=1, name="x", props={}))  # breaks the transaction

        monkeypatch.setattr(box, "tool_update_brief", boom)
        assert _openai_agent(box).respond(box, [], "hi")["reply"] == "All set."


def test_daily_llm_budget_falls_back_to_the_offline_assistant(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "not-used")
    monkeypatch.setattr(settings, "llm_tokens_per_day", 100)
    monkeypatch.setattr(agent_module.OpenAIAgent, "__init__", lambda self: setattr(self, "client", None))
    assert agent_module.get_agent().mode == "openai"
    limits.record_llm_tokens(100)
    assert agent_module.get_agent().mode == "offline"


def test_llm_tokens_count_toward_the_budget(monkeypatch):
    monkeypatch.setattr(settings, "llm_tokens_per_day", 40)
    with SessionLocal() as session:
        box = _box(session)
        _openai_agent(box, tokens=10).respond(box, [], "hi")  # two calls, 20 tokens each
        assert not limits.llm_budget_left()
