"""Flight search adapters. All return the same FlightOffer shape so sources can be swapped."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import httpx

from .. import clock, geo
from ..cache import get_json, set_json
from ..config import DATA_DIR, settings

log = logging.getLogger(__name__)


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
    stops: int
    fare_class: str  # "main" | "basic"
    fare: float  # per traveler, USD, taxes included
    kind: str  # "quote" (live) | "demo"
    source: str
    as_of: str
    booking_url: str

    @property
    def depart_dt(self) -> datetime:
        return datetime.fromisoformat(self.depart)

    @property
    def arrive_dt(self) -> datetime:
        return datetime.fromisoformat(self.arrive)


class FlightSearchError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@lru_cache
def _fixture_routes() -> dict:
    return json.loads((DATA_DIR / "fixture_routes.json").read_text())


def _seeded(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


class FixtureFlights:
    """Deterministic demo fares for the launch market. Same inputs, same prices."""

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
                seed = _seeded(origin, destination, code, hhmm, day.isoformat())
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
                            stops=0,
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
        m = 0.84 + (seed % 37) / 100  # 0.84–1.20 day-to-day noise
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


class AmadeusFlights:
    """Amadeus Flight Offers Search (v2). Requires AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET.

    Not yet exercised against a live account; verify field mapping when keys arrive.
    """

    name = "amadeus"
    source = "Amadeus flight offers"

    def __init__(self) -> None:
        self._token: tuple[str, datetime] | None = None

    def _auth(self, client: httpx.Client) -> str:
        if self._token and self._token[1] > datetime.now(timezone.utc):
            return self._token[0]
        resp = client.post(
            f"{settings.amadeus_host}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.amadeus_client_id,
                "client_secret": settings.amadeus_client_secret,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = (body["access_token"], datetime.now(timezone.utc) + timedelta(seconds=body["expires_in"] - 60))
        return self._token[0]

    def search(self, origin: str, destination: str, day: date) -> list[FlightOffer]:
        try:
            with httpx.Client(timeout=20) as client:
                token = self._auth(client)
                resp = client.get(
                    f"{settings.amadeus_host}/v2/shopping/flight-offers",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "originLocationCode": origin,
                        "destinationLocationCode": destination,
                        "departureDate": day.isoformat(),
                        "adults": 1,
                        "nonStop": "true",
                        "currencyCode": "USD",
                        "max": 30,
                    },
                )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise FlightSearchError(f"Amadeus search failed: {exc}") from exc
        return self._parse(resp.json(), origin, destination)

    def _parse(self, body: dict, origin: str, destination: str) -> list[FlightOffer]:
        as_of = _now_iso()
        o_tz, d_tz = ZoneInfo(geo.airport(origin)["tz"]), ZoneInfo(geo.airport(destination)["tz"])
        offers = []
        for item in body.get("data", []):
            segments = item["itineraries"][0]["segments"]
            first, last = segments[0], segments[-1]
            code = first["carrierCode"]
            if code not in geo.airport_data()["airlines"]:
                continue  # bag-fee table doesn't cover this carrier yet
            fare_detail = item["travelerPricings"][0]["fareDetailsBySegment"][0]
            branded = (fare_detail.get("brandedFare") or "").upper()
            depart = datetime.fromisoformat(first["departure"]["at"]).replace(tzinfo=o_tz)
            arrive = datetime.fromisoformat(last["arrival"]["at"]).replace(tzinfo=d_tz)
            offers.append(
                FlightOffer(
                    id=f"amadeus-{item['id']}-{code}{first['number']}",
                    airline=code,
                    airline_name=geo.airline(code)["name"],
                    flight_number=f"{code}{first['number']}",
                    origin=origin,
                    destination=destination,
                    depart=depart.isoformat(),
                    arrive=arrive.isoformat(),
                    duration_min=int((arrive - depart).total_seconds() // 60),
                    stops=len(segments) - 1,
                    fare_class="basic" if "BASIC" in branded else "main",
                    fare=float(item["price"]["grandTotal"]),
                    kind="quote",
                    source=self.source,
                    as_of=as_of,
                    booking_url=geo.airline(code)["site"],
                )
            )
        return offers


def get_flight_adapter():
    return AmadeusFlights() if settings.flight_data_mode == "live" else FixtureFlights()


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
