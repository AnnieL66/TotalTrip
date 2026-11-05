"""Flight sources. Each returns the same FlightOffer shape, so a live provider can be added next to the demo fares."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from .. import clock, geo
from ..cache import get_json, set_json
from ..config import DATA_DIR, settings


@dataclass
class FlightOffer:
    id: str
    airline: str
    airline_name: str
    flight_number: str
    origin: str
    destination: str
    depart: str  # ISO 8601 with offset, local to origin airport
    arrive: str  # ISO 8601 with offset, local to destination airport
    duration_min: int
    fare_class: str  # "main" | "basic"
    fare: float  # per traveler, USD, taxes included
    kind: str  # "quote" | "demo"
    source: str
    as_of: str
    booking_url: str

    @property
    def depart_dt(self) -> datetime:
        return datetime.fromisoformat(self.depart)

    @property
    def arrive_dt(self) -> datetime:
        return datetime.fromisoformat(self.arrive)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@lru_cache
def _fixture_routes() -> dict:
    return json.loads((DATA_DIR / "fixture_routes.json").read_text())


def _seeded(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


class FixtureFlights:
    """Deterministic demo fares for the launch market. Same inputs, same prices.

    A flight keeps its number, length and base price every day, so demo trips tell the same story
    whatever date they run on. Fares only move with the day of the week and how far ahead it is.
    """

    name = "demo"
    source = "Demo fares (illustrative)"

    def search(self, origin: str, destination: str, day: date) -> list[FlightOffer]:
        data = _fixture_routes()
        o_ap, d_ap = geo.airport(origin), geo.airport(destination)
        block = data["block_minutes"][f"{o_ap['metro']}-{d_ap['metro']}"]
        o_tz, d_tz = ZoneInfo(o_ap["tz"]), ZoneInfo(d_ap["tz"])
        as_of = _now_iso()
        offers: list[FlightOffer] = []
        for route in data["routes"]:
            if route["origin"] != origin or route["destination"] != destination:
                continue
            code = route["airline"]
            for hhmm in route["times"]:
                seed = _seeded(origin, destination, code, hhmm)
                h, m = map(int, hhmm.split(":"))
                depart = datetime(day.year, day.month, day.day, h, m, tzinfo=o_tz)
                duration = block + (seed % 3) * 5
                arrive = (depart + timedelta(minutes=duration)).astimezone(d_tz)
                number = f"{code}{100 + seed % 1800}"
                for fare_class, base in route["fares"].items():
                    offers.append(
                        FlightOffer(
                            id=f"{number}-{day.isoformat()}-{fare_class}",
                            airline=code,
                            airline_name=geo.airline(code)["name"],
                            flight_number=number,
                            origin=origin,
                            destination=destination,
                            depart=depart.isoformat(),
                            arrive=arrive.isoformat(),
                            duration_min=duration,
                            fare_class=fare_class,
                            fare=float(round(base * self._multiplier(seed, depart, day))),
                            kind="demo",
                            source=self.source,
                            as_of=as_of,
                            booking_url=geo.airline(code)["site"],
                        )
                    )
        return offers

    @staticmethod
    def _multiplier(seed: int, depart: datetime, day: date) -> float:
        m = 0.84 + (seed % 37) / 100  # 0.84–1.20 per flight
        m *= {4: 1.12, 6: 1.12, 1: 0.94, 2: 0.94}.get(day.weekday(), 1.0)
        if depart.hour < 7:
            m *= 0.9
        elif 16 <= depart.hour < 19:
            m *= 1.08
        lead = (day - clock.today()).days
        if lead < 7:
            m *= 1.45
        elif lead < 14:
            m *= 1.25
        return m


def get_flight_adapter():
    return FixtureFlights()


def search_cached(adapter, origin: str, destination: str, day: date, refresh: bool = False) -> list[FlightOffer]:
    """Cache searches so repeated plan views don't re-query (and re-pay for) the same route."""
    key = f"flights:{adapter.name}:{origin}:{destination}:{day.isoformat()}"
    if not refresh:
        cached = get_json(key)
        if cached is not None:
            return [FlightOffer(**o) for o in cached]
    offers = adapter.search(origin, destination, day)
    set_json(key, [asdict(o) for o in offers], settings.flight_cache_ttl)
    return offers
