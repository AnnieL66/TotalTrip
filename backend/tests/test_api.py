from fastapi.testclient import TestClient

from app.main import app


def test_conversation_to_plans_to_click():
    with TestClient(app) as client:
        r = client.post("/api/trips", json={"message": "Upper West Side to Millennium Park, Nov 12-16, one checked bag"})
        assert r.status_code == 200
        trip = r.json()
        assert trip["brief"]["ready"]
        assert trip["assistant_mode"] == "offline"
        fields = {f["name"]: f for f in trip["brief"]["fields"]}
        assert fields["origin"]["provenance"] == "user_said"
        assert fields["priority"]["provenance"] == "assumed"
        assert [m["role"] for m in trip["messages"]] == ["assistant", "user", "assistant"]

        r = client.post(f"/api/trips/{trip['id']}/plans")
        assert r.status_code == 200
        plans = r.json()
        assert plans["is_current"] and plans["plans"]

        # Editing the brief marks plans stale
        r = client.patch(f"/api/trips/{trip['id']}/brief", json={"budget": 600})
        assert r.json()["plans"]["is_current"] is False

        # Asking about plans gets an answer built from engine numbers
        r = client.post(f"/api/trips/{trip['id']}/messages", json={"text": "Why is plan B better?"})
        assert "$" in r.json()["reply"]

        plan = plans["plans"][0]
        url = plan["booking_links"][0]["url"]
        r = client.get("/api/out", params={"url": url, "trip": trip["id"], "plan": plan["id"], "search": plans["search_id"]}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == url

        r = client.post(f"/api/trips/{trip['id']}/spend", json={"plan_id": plan["id"], "search_id": plans["search_id"], "actual": {"flights": 300, "rides": 120}})
        assert r.json()["estimated_total"] == plan["totals"]["likely"]

        m = client.get("/api/admin/metrics").json()
        assert m["trips_clicked_to_book"] >= 1 and m["spend_reports"] >= 1


def test_open_redirect_blocked():
    with TestClient(app) as client:
        assert client.get("/api/out", params={"url": "https://evil.example.com"}, follow_redirects=False).status_code == 400


def test_search_requires_complete_brief():
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={}).json()
        r = client.post(f"/api/trips/{trip['id']}/plans")
        assert r.status_code == 422


def test_ai_cannot_search_until_asked():
    with TestClient(app) as client:
        trip = client.post("/api/trips", json={"message": "from Harlem to Wicker Park Nov 20 to Nov 23"}).json()
        assert trip["plans"] is None
        r = client.post(f"/api/trips/{trip['id']}/messages", json={"text": "compare plans"}).json()
        assert r["plans_searched"] and r["plans"]["plans"]
