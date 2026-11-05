"""Drive time and distance. Google Routes when a key is set, otherwise a rate-card estimate."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .. import geo
from ..cache import get_json, set_json
from ..config import settings
from ..engine.rates import rates

log = logging.getLogger(__name__)


@dataclass
class DriveEstimate:
    miles: float
    minutes: float
    source: str


def is_peak(when: datetime) -> bool:
    return any(start <= when.hour < end for start, end in rates()["rideshare"]["peak_hours"])


class EstimateRouting:
    name = "estimate"

    def drive(self, a: tuple[float, float], b: tuple[float, float], metro: str, when: datetime) -> DriveEstimate:
        card = rates()["rideshare"][metro]
        miles = geo.haversine_mi(a, b) * card["road_factor"]
        minutes = miles / card["avg_mph"] * 60 + 6
        if is_peak(when):
            minutes *= rates()["rideshare"]["peak_time_factor"]
        return DriveEstimate(round(miles, 1), round(minutes), "TotalTrip distance estimate")


class GoogleRouting:
    """Google Routes API computeRoutes with traffic-aware timing. Falls back to estimates on failure."""

    name = "google"
    URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

    def __init__(self) -> None:
        self._fallback = EstimateRouting()

    def drive(self, a: tuple[float, float], b: tuple[float, float], metro: str, when: datetime) -> DriveEstimate:
        key = f"route:{a}:{b}:{when.strftime('%a%H')}"
        cached = get_json(key)
        if cached:
            return DriveEstimate(**cached)
        body = {
            "origin": {"location": {"latLng": {"latitude": a[0], "longitude": a[1]}}},
            "destination": {"location": {"latLng": {"latitude": b[0], "longitude": b[1]}}},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
        }
        if when > datetime.now(timezone.utc):
            body["departureTime"] = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            resp = httpx.post(
                self.URL,
                json=body,
                headers={"X-Goog-Api-Key": settings.google_maps_api_key, "X-Goog-FieldMask": "routes.duration,routes.distanceMeters"},
                timeout=10,
            )
            resp.raise_for_status()
            route = resp.json()["routes"][0]
            est = DriveEstimate(
                miles=round(route["distanceMeters"] / 1609.34, 1),
                minutes=round(int(route["duration"].rstrip("s")) / 60),
                source="Google Routes",
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("Google Routes failed, using estimate: %s", exc)
            return self._fallback.drive(a, b, metro, when)
        set_json(key, est.__dict__, 7 * 24 * 3600)
        return est


def get_routing():
    return GoogleRouting() if settings.google_maps_api_key else EstimateRouting()
