"""Small cache facade: Redis when REDIS_URL is set, otherwise an in-process TTL dict."""
import json
import threading
import time
from typing import Any

from .config import settings

MAX_MEMORY_KEYS = 10_000


class _MemoryCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str]] = {}
        self._lock = threading.RLock()

    def _drop_expired_if_full(self) -> None:
        if len(self._store) >= MAX_MEMORY_KEYS:
            now = time.time()
            self._store = {k: item for k, item in self._store.items() if item[0] >= now}

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expires, value = item
            if expires < time.time():
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: str, ttl: int) -> None:
        with self._lock:
            self._drop_expired_if_full()
            self._store[key] = (time.time() + ttl, value)

    def incr(self, key: str, ttl: int, amount: int = 1) -> int:
        with self._lock:
            self._drop_expired_if_full()
            current = int(self.get(key) or 0) + amount
            expires = self._store[key][0] if key in self._store else time.time() + ttl
            self._store[key] = (expires, str(current))
            return current

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class _RedisCache:
    def __init__(self, url: str) -> None:
        import redis

        self._r = redis.Redis.from_url(url, decode_responses=True)

    def get(self, key: str) -> str | None:
        return self._r.get(key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self._r.set(key, value, ex=ttl)

    def incr(self, key: str, ttl: int, amount: int = 1) -> int:
        pipe = self._r.pipeline()
        pipe.incrby(key, amount)
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
