"""Reference tables used by the cost engine (bag fees, ride rate cards, transit fares, parking)."""
import json
from functools import lru_cache

from ..config import DATA_DIR


@lru_cache
def rates() -> dict:
    return json.loads((DATA_DIR / "ground_rates.json").read_text())


@lru_cache
def bag_fees() -> dict:
    return json.loads((DATA_DIR / "bag_fees.json").read_text())
