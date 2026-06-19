"""
Примитивы управления джобами очереди (идемпотентность + per-user fairness).

Работают поверх async-redis из контекста arq (ctx["redis"]). Параметры берутся
из env, чтобы тюнить без передеплоя.

Идемпотентность: лок `SET NX EX` на сущность (CRM-запись или chat-вложение) —
повторный/ретраенный enqueue не запустит второй анализ того же объекта.

Fairness: «семафор на юзера» в виде sorted-set с таймстемпами (как sliding-window
в middleware/rate_limit.py): протухшие слоты (от упавших воркеров) подметаются по
TTL, поэтому утечки слотов самоисцеляются.
"""
import os
import time
import logging

logger = logging.getLogger("main")

PER_USER_CONCURRENCY = int(os.getenv("PER_USER_CONCURRENCY", "2"))
USER_SLOT_TTL = int(os.getenv("USER_SLOT_TTL", "2400"))          # > job_timeout
IDEMPOTENCY_LOCK_TTL = int(os.getenv("IDEMPOTENCY_LOCK_TTL", "2400"))
FAIRNESS_DEFER_SECONDS = int(os.getenv("FAIRNESS_DEFER_SECONDS", "15"))
FAIRNESS_MAX_ATTEMPTS = int(os.getenv("FAIRNESS_MAX_ATTEMPTS", "40"))


# ── Идемпотентность ───────────────────────────────────────

async def acquire_lock(redis, key: str, ttl: int = IDEMPOTENCY_LOCK_TTL) -> bool:
    """True, если лок взят (объект свободен). False — уже обрабатывается."""
    try:
        got = await redis.set(key, "1", nx=True, ex=ttl)
        return bool(got)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"acquire_lock({key}) failed, proceeding without lock: {e}")
        return True  # fail-open: лучше обработать, чем потерять анализ


async def release_lock(redis, key: str) -> None:
    try:
        await redis.delete(key)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"release_lock({key}) failed: {e}")


# ── Per-user fairness (семафор со скользящим окном) ───────

def _slots_key(user_id) -> str:
    return f"fairness:user:{user_id}:slots"


async def acquire_user_slot(redis, user_id, token: str,
                            cap: int = PER_USER_CONCURRENCY, ttl: int = USER_SLOT_TTL) -> bool:
    """Пытается занять слот юзера. False — у юзера уже cap активных анализов."""
    key = _slots_key(user_id)
    now = time.time()
    try:
        # подметаем протухшие слоты (упавшие воркеры)
        await redis.zremrangebyscore(key, 0, now - ttl)
        count = await redis.zcard(key)
        if count >= cap:
            return False
        await redis.zadd(key, {token: now})
        await redis.expire(key, ttl * 2)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"acquire_user_slot(user={user_id}) failed, proceeding: {e}")
        return True  # fail-open


async def release_user_slot(redis, user_id, token: str) -> None:
    try:
        await redis.zrem(_slots_key(user_id), token)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"release_user_slot(user={user_id}) failed: {e}")
