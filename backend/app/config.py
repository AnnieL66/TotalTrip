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
        self.google_maps_api_key = _env("GOOGLE_MAPS_API_KEY")
        self.flight_cache_ttl = int(_env("FLIGHT_CACHE_TTL", "900"))
        self.searches_per_hour = int(_env("SEARCHES_PER_HOUR", "20"))
        self.cors_origins = (_env("CORS_ORIGINS", "http://localhost:3000") or "").split(",")
        self.admin_token = _env("ADMIN_TOKEN")
        self.tester_codes = {c.strip() for c in (_env("TESTER_CODES") or "").split(",") if c.strip()}
        self.trips_per_hour_per_ip = int(_env("TRIPS_PER_HOUR_PER_IP", "30"))
        self.messages_per_hour_per_ip = int(_env("MESSAGES_PER_HOUR_PER_IP", "120"))
        self.searches_per_hour_per_ip = int(_env("SEARCHES_PER_HOUR_PER_IP", "60"))
        self.llm_tokens_per_day = int(_env("LLM_TOKENS_PER_DAY", "500000"))
        self.max_request_bytes = int(_env("MAX_REQUEST_BYTES", "65536"))
        # Load balancers that add the visitor's address to X-Forwarded-For. The Next.js rewrite isn't one: it passes the header on unchanged.
        self.trusted_proxy_hops = int(_env("TRUSTED_PROXY_HOPS", "0"))

    @property
    def assistant_mode(self) -> str:
        return "openai" if self.openai_api_key else "offline"


settings = Settings()
