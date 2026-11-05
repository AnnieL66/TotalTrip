import os
import tempfile
from datetime import date, datetime, timezone

# Isolated DB and offline mode for every test run; must be set before the app is imported.
_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_db.name}"
for key in ("OPENAI_API_KEY", "AMADEUS_CLIENT_ID", "AMADEUS_CLIENT_SECRET", "GOOGLE_MAPS_API_KEY", "REDIS_URL"):
    os.environ[key] = ""

import pytest  # noqa: E402

from app import brief as B  # noqa: E402
from app import clock  # noqa: E402
from app.cache import cache  # noqa: E402


def set_clock(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(clock, "now", lambda: moment)
    monkeypatch.setattr(clock, "today", lambda: moment.date())


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch):
    """Tests use fixed trip dates, so they run at a fixed time and don't expire."""
    set_clock(monkeypatch, datetime(2026, 10, 17, 16, 0, tzinfo=timezone.utc))
    assert clock.today() == date(2026, 10, 17)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield


@pytest.fixture
def sample_brief():
    """The deck's example trip, solo: Upper West Side → Millennium Park, Nov 12–16."""
    b, errors = B.apply_updates(
        B.empty_brief(),
        {"origin": "Upper West Side", "destination": "Millennium Park", "depart_date": "2026-11-12", "return_date": "2026-11-16"},
        "user_said",
    )
    assert not errors
    return B.with_defaults(b)
