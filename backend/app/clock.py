"""The current time, in one place so tests can pin it."""
from datetime import date, datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    return date.today()
