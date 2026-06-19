"""
Rate-limiting middleware.

Redis sliding-window (ZADD + ZREMRANGEBYSCORE) is used when REDIS_URL is set.
Falls back to per-process in-memory counter with a warning — sufficient for
single-worker dev, but NOT accurate across multiple workers.
"""
import hashlib
import os
import time
import uuid
import logging
from collections import defaultdict
from typing import Dict, Optional, Tuple

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)

# ── Redis backend ────────────────────────────────────────────────────────────

_redis_client = None
_redis_warned = False


def _get_redis():
    global _redis_client, _redis_warned
    if _redis_client is not None:
        return _redis_client
    url = os.getenv("REDIS_URL", "")
    if not url:
        if not _redis_warned:
            logger.warning(
                "REDIS_URL not set — rate limiter using in-memory fallback "
                "(not shared across workers)"
            )
            _redis_warned = True
        return None
    try:
        import redis as _redis
        _redis_client = _redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        _redis_client.ping()
        logger.info("Rate limiter: connected to Redis", extra={"url": url[:30]})
        return _redis_client
    except Exception as exc:
        if not _redis_warned:
            logger.warning(
                "Rate limiter: Redis unavailable, falling back to in-memory",
                extra={"error": str(exc)},
            )
            _redis_warned = True
        return None


def _redis_is_limited(r, key: str, max_requests: int, window_seconds: int) -> Tuple[bool, Optional[int]]:
    """Sliding window via sorted set. Returns (limited, retry_after_seconds)."""
    now = time.time()
    # Член множества должен быть уникальным: иначе два запроса с одинаковым
    # time.time() схлопнутся в один элемент (ZADD перезапишет), и лимитер
    # будет недосчитывать. Score = now (для окна), member = now:uuid (уникален).
    member = f"{now}:{uuid.uuid4().hex}"
    pipe = r.pipeline()
    pipe.zremrangebyscore(key, "-inf", now - window_seconds)
    pipe.zadd(key, {member: now})
    pipe.zcard(key)
    pipe.expire(key, window_seconds + 1)
    _, _, count, _ = pipe.execute()

    if count > max_requests:
        # Overshoot — remove the just-added entry, return retry info
        r.zrem(key, member)
        oldest = r.zrange(key, 0, 0, withscores=True)
        if oldest:
            retry = int(window_seconds - (now - oldest[0][1])) + 1
        else:
            retry = window_seconds
        return True, max(1, retry)
    return False, None


# ── In-memory fallback ───────────────────────────────────────────────────────

class _InMemoryLimiter:
    def __init__(self):
        self.requests: Dict[str, list] = defaultdict(list)
        self.last_cleanup = time.time()

    def _cleanup(self):
        now = time.time()
        if now - self.last_cleanup < 300:
            return
        cutoff = now - 3600
        for k in list(self.requests):
            self.requests[k] = [(ts, c) for ts, c in self.requests[k] if ts > cutoff]
            if not self.requests[k]:
                del self.requests[k]
        self.last_cleanup = now

    def is_limited(self, key: str, max_requests: int, window_seconds: int) -> Tuple[bool, Optional[int]]:
        self._cleanup()
        now = time.time()
        cutoff = now - window_seconds
        recent = [(ts, c) for ts, c in self.requests[key] if ts > cutoff]
        total = sum(c for _, c in recent)
        if total >= max_requests:
            oldest = min(ts for ts, _ in recent) if recent else now
            retry = int(window_seconds - (now - oldest)) + 1
            return True, max(1, retry)
        self.requests[key].append((now, 1))
        return False, None


_fallback = _InMemoryLimiter()


def _is_rate_limited(key: str, max_requests: int, window_seconds: int) -> Tuple[bool, Optional[int]]:
    r = _get_redis()
    if r is not None:
        try:
            return _redis_is_limited(r, f"rl:{key}", max_requests, window_seconds)
        except Exception as exc:
            logger.warning("Redis rate-limit error, falling back", extra={"error": str(exc)})
    return _fallback.is_limited(key, max_requests, window_seconds)


# ── Middleware ───────────────────────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    RULES = {
        "default":       (100, 60),
        "api":           (30,  60),
        "upload":        (10,  300),
        "auth":          (5,   60),
        "authenticated": (200, 60),
    }

    STRICT = {
        "/login":            "auth",
        "/register":         "auth",
        "/chat/send":        "upload",
        "/chat_trener/send": "upload",
    }

    API_PREFIXES = ("/api/", "/voice-training/", "/voice-assistant/")

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith("/static/"):
            return await call_next(request)

        client_id = self._client_id(request)
        rule_name, max_req, window = self._rule(request)
        key = f"{client_id}:{rule_name}"

        limited, retry = _is_rate_limited(key, max_req, window)
        if limited:
            logger.warning(
                "Rate limit exceeded",
                extra={"client": client_id, "path": request.url.path, "rule": rule_name, "retry": retry},
            )
            return Response(
                content=f"Слишком много запросов. Повторите через {retry} с.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={
                    "Retry-After": str(retry),
                    "X-RateLimit-Limit": str(max_req),
                    "X-RateLimit-Window": str(window),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(max_req)
        response.headers["X-RateLimit-Window"] = str(window)
        return response

    @staticmethod
    def _client_id(request: Request) -> str:
        if "session" in request.scope:
            uid = request.session.get("user_id")
            if uid:
                return f"user_{uid}"
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else "unknown"
        )
        return "ip_" + hashlib.sha256(ip.encode()).hexdigest()[:16]

    def _rule(self, request: Request) -> Tuple[str, int, int]:
        path = request.url.path
        if path in self.STRICT:
            name = self.STRICT[path]
            return name, *self.RULES[name]
        for prefix in self.API_PREFIXES:
            if path.startswith(prefix):
                return "api", *self.RULES["api"]
        if "session" in request.scope and request.session.get("user_id"):
            return "authenticated", *self.RULES["authenticated"]
        return "default", *self.RULES["default"]


def get_rate_limiter():
    return _fallback
