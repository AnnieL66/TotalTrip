"""Launch-market places, airports and distance helpers."""
import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache

from .config import DATA_DIR


@dataclass(frozen=True)
class Place:
    key: str
    name: str
    metro: str
    lat: float
    lng: float
    flags: tuple[str, ...] = ()
    generic: bool = False

    @property
    def point(self) -> tuple[float, float]:
        return (self.lat, self.lng)


@lru_cache
def _places() -> dict[str, Place]:
    raw = json.loads((DATA_DIR / "places.json").read_text())["places"]
    return {
        p["key"]: Place(p["key"], p["name"], p["metro"], p["lat"], p["lng"], tuple(p.get("flags", [])), p.get("generic", False))
        for p in raw
    }


@lru_cache
def _alias_patterns() -> list[tuple[re.Pattern, str]]:
    raw = json.loads((DATA_DIR / "places.json").read_text())["places"]
    pairs = [(alias, p["key"]) for p in raw for alias in p["aliases"]]
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)  # longest alias wins
    return [(re.compile(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", re.I), key) for alias, key in pairs]


@lru_cache
def airport_data() -> dict:
    return json.loads((DATA_DIR / "airports.json").read_text())


def airport(code: str) -> dict:
    return airport_data()["airports"][code]


def airline(code: str) -> dict:
    return airport_data()["airlines"][code]


def metro_airports(metro: str) -> list[str]:
    return airport_data()["metros"][metro]["airports"]


def all_places() -> list[Place]:
    return list(_places().values())


def get_place(key: str) -> Place | None:
    return _places().get(key)


def straighten_quotes(text: str) -> str:
    """Phone keyboards type ’ where our patterns expect '. Keeps every character's position."""
    return text.replace("’", "'").replace("‘", "'")


def find_place_spans(text: str) -> list[tuple[int, int, Place]]:
    """All places mentioned in free text, as (start, end, place), in order and without overlapping matches."""
    text = straighten_quotes(text)
    found: list[tuple[int, int, Place]] = []
    for pattern, key in _alias_patterns():
        for m in pattern.finditer(text):
            if any(s < m.end() and m.start() < e for s, e, _ in found):
                continue
            found.append((m.start(), m.end(), _places()[key]))
    return sorted(found, key=lambda f: f[0])


def find_places(text: str) -> list[tuple[int, Place]]:
    return [(start, place) for start, _, place in find_place_spans(text)]


def resolve_place(text: str) -> Place | None:
    """Best single place for a short location string; specific places beat generic city names."""
    matches = [place for _, place in find_places(text)]
    specific = [p for p in matches if not p.generic]
    if specific:
        return specific[0]
    return matches[0] if matches else None


def haversine_mi(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))
