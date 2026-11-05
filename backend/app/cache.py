"""Small cache facade: Redis when REDIS_URL is set, otherwise an in-process TTL dict."""
import json
import time
from typing import Any

from .config import settings


class _MemoryCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        item = self._store.get(key)
        if not item:
            return None
        expires, value = item
        if expires < time.time():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str, ttl: int) -> None:
        self._store[key] = (time.time() + ttl, value)

    def incr(self, key: str, ttl: int) -> int:
        current = int(self.get(key) or 0) + 1
        expires = self._store[key][0] if key in self._store else time.time() + ttl
        self._store[key] = (expires, str(current))
        return current

    def clear(self) -> None:
        self._store.clear()


class _RedisCache:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True)

    def get(self, key: str) -> str | None:
        return self._r.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self._r.set(key, value, ex=ttl)

    def incr(self, key: str, ttl: int) -> int:
        pipe = self._r.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl, nx=True)
        return int(pipe.execute()[0])

    def clear(self) -> None:
        self._r.flushdb()


cache = _RedisCache(settings.redis_url) if settings.redis_url else _MemoryCache()


def get_json(key: str) -> Any | None:
    raw = cache.get(key)
    return json.loads(raw) if raw else None


def set_json(key: str, value: Any, ttl: int) -> None:
    cache.set(key, json.dumps(value, default=str), ttl)
