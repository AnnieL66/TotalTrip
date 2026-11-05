"""Runtime settings. Every external service is optional; without keys the app runs on demo data."""
import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
DATA_DIR = APP_DIR / "data"

load_dotenv(BACKEND_DIR.parent / ".env")
load_dotenv(BACKEND_DIR / ".env")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value else default


class Settings:
    def __init__(self) -> None:
        self.database_url = _env("DATABASE_URL", f"sqlite:///{BACKEND_DIR / 'totaltrip.db'}")
        self.redis_url = _env("REDIS_URL")
        self.openai_api_key = _env("OPENAI_API_KEY")
        self.openai_model = _env("OPENAI_MODEL", "gpt-4.1-mini")
        self.amadeus_client_id = _env("AMADEUS_CLIENT_ID")
        self.amadeus_client_secret = _env("AMADEUS_CLIENT_SECRET")
        self.amadeus_host = _env("AMADEUS_HOST", "https://test.api.amadeus.com")
        self.google_maps_api_key = _env("GOOGLE_MAPS_API_KEY")
        self.flight_cache_ttl = int(_env("FLIGHT_CACHE_TTL", "900"))
        self.searches_per_hour = int(_env("SEARCHES_PER_HOUR", "20"))
        self.cors_origins = (_env("CORS_ORIGINS", "http://localhost:3000") or "").split(",")

    @property
    def assistant_mode(self) -> str:
        return "openai" if self.openai_api_key else "offline"

    @property
    def flight_data_mode(self) -> str:
        return "live" if self.amadeus_client_id and self.amadeus_client_secret else "demo"


settings = Settings()
