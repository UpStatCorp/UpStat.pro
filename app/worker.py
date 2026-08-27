"""
arq worker — выполняет тяжёлые анализы из очереди (Redis).

Запуск (см. docker-compose сервис `worker`):
    arq worker.WorkerSettings

Гарантии:
- Глобальный потолок одновременных анализов = WORKER_REPLICAS × max_jobs.
- Идемпотентность: на каждый объект (CRM-запись / chat-вложение) берётся Redis-лок —
  повтор/ретрай не запустит второй анализ того же объекта.
- Fairness: на юзера не больше PER_USER_CONCURRENCY активных анализов; сверх лимита
  джоб переставляется в очередь с задержкой (с ограничением числа попыток).

Модуль загружается ТОЛЬКО worker-процессом (не вебом), поэтому arq импортируется
на верхнем уровне. Тяжёлые модули анализа импортируются лениво внутри джобов.
"""
import os
import logging

from arq.connections import RedisSettings

from services.job_control import (
    acquire_lock, release_lock, acquire_user_slot, release_user_slot,
    FAIRNESS_DEFER_SECONDS, FAIRNESS_MAX_ATTEMPTS,
)

logger = logging.getLogger("main")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


async def _run_with_controls(ctx, *, lock_key, user_id, attempt, run_factory, reenqueue):
    """Общая обвязка: fairness-гейт → идемпотентность-лок → выполнение."""
    redis = ctx["redis"]
    token = str(ctx.get("job_id") or id(ctx))

    # 1. Fairness: не больше PER_USER_CONCURRENCY активных анализов на юзера
    slot_taken = False
    if user_id is not None:
        if await acquire_user_slot(redis, user_id, token):
            slot_taken = True
        elif attempt < FAIRNESS_MAX_ATTEMPTS:
            logger.info(f"[worker] user {user_id} at concurrency cap — defer (attempt {attempt})")
            await reenqueue(attempt + 1)
            return
        # попытки исчерпаны → выполняем без слота, чтобы не зависнуть навсегда

    try:
        # 2. Идемпотентность: не запускать второй анализ того же объекта
        if not await acquire_lock(redis, lock_key):
            logger.info(f"[worker] {lock_key} already in progress — skip duplicate")
            return
        try:
            await run_factory()
        finally:
            await release_lock(redis, lock_key)
    finally:
        if slot_taken:
            await release_user_slot(redis, user_id, token)


# ── Джобы ─────────────────────────────────────────────────

async def analyze_recording_job(ctx, recording_id: int, owner_id=None, attempt: int = 0):
    """Анализ одной CRM-записи (single или элемент батча)."""
    logger.info(f"[worker] analyze_recording_job recording_id={recording_id} owner={owner_id}")

    async def _reenqueue(next_attempt):
        await ctx["redis"].enqueue_job(
            "analyze_recording_job", recording_id, owner_id, next_attempt,
            _defer_by=FAIRNESS_DEFER_SECONDS,
        )

    async def _run():
        from routers.crm_integration import analyze_recording_task
        await analyze_recording_task(recording_id)

    await _run_with_controls(
        ctx,
        lock_key=f"lock:analyze:rec:{recording_id}",
        user_id=owner_id,
        attempt=attempt,
        run_factory=_run,
        reenqueue=_reenqueue,
    )


async def run_pipeline_job(ctx, kind: str, user_id: int, conversation_id: int, ref, progress_conversation_id=None, attempt: int = 0):
    """Анализ из чата: audio/text/raw_text/images и их *_trener-варианты.

    ref: attachment_id (int) | raw_text (str) | список image_attachment_ids (list).
    """
    logger.info(f"[worker] run_pipeline_job kind={kind} conv={conversation_id} user={user_id}")
    ref_token = "-".join(map(str, ref)) if isinstance(ref, (list, tuple)) else str(ref)
    lock_key = f"lock:pipeline:{conversation_id}:{kind}:{ref_token}"

    async def _reenqueue(next_attempt):
        await ctx["redis"].enqueue_job(
            "run_pipeline_job", kind, user_id, conversation_id, ref, progress_conversation_id, next_attempt,
            _defer_by=FAIRNESS_DEFER_SECONDS,
        )

    async def _run():
        from services.pipeline import run_pipeline, run_pipeline_from_text, run_pipeline_from_raw_text
        from services.image_pipeline import run_pipeline_from_images, run_pipeline_from_images_trener
        from services.pipeline_trener import (
            run_pipeline_trener, run_pipeline_from_text_trener, run_pipeline_from_raw_text_trener,
        )
        if kind == "audio":
            await run_pipeline(user_id, conversation_id, ref, progress_conversation_id=progress_conversation_id)
        elif kind == "text":
            await run_pipeline_from_text(user_id, conversation_id, ref, progress_conversation_id=progress_conversation_id)
        elif kind == "raw_text":
            await run_pipeline_from_raw_text(user_id, conversation_id, ref, progress_conversation_id=progress_conversation_id)
        elif kind == "images":
            await run_pipeline_from_images(user_id, conversation_id, ref, progress_conversation_id=progress_conversation_id)
        elif kind == "audio_trener":
            await run_pipeline_trener(user_id, conversation_id, ref)
        elif kind == "text_trener":
            await run_pipeline_from_text_trener(user_id, conversation_id, ref)
        elif kind == "raw_text_trener":
            await run_pipeline_from_raw_text_trener(user_id, conversation_id, ref)
        elif kind == "images_trener":
            await run_pipeline_from_images_trener(user_id, conversation_id, ref)
        else:
            raise ValueError(f"Unknown pipeline kind: {kind!r}")

    await _run_with_controls(
        ctx,
        lock_key=lock_key,
        user_id=user_id,
        attempt=attempt,
        run_factory=_run,
        reenqueue=_reenqueue,
    )


# ── Настройки воркера ─────────────────────────────────────

async def _startup(ctx):
    """
    Та же проверка схемы, что и у веба. Без неё получалась дыра: веб падал
    на рассинхроне, а воркеры продолжали писать в несогласованную базу —
    create_all вызывался только в create_app(), воркер не проверял ничего.
    """
    from db_guard import assert_schema_current
    from services.crm_service import assert_encryption_key_valid

    assert_schema_current()
    # Воркер расшифровывает те же токены, что и веб (анализ CRM-записей),
    # поэтому проверяет ключ тем же способом.
    assert_encryption_key_valid()


class WorkerSettings:
    functions = [analyze_recording_job, run_pipeline_job]
    on_startup = _startup
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    # Глобальный потолок одновременных анализов на процесс воркера.
    # Итоговый потолок системы = WORKER_REPLICAS × max_jobs.
    max_jobs = int(os.getenv("WORKER_MAX_JOBS", "8"))
    # Долгий анализ (транскрибация + ~10 LLM-вызовов) — даём запас.
    job_timeout = int(os.getenv("WORKER_JOB_TIMEOUT", "1800"))
    # ВАЖНО: пайплайны анализа НЕ идемпотентны — повторный прогон после частичного
    # сбоя/краша создаёт дубли сообщений и (для CRM) новую Conversation. Поэтому по
    # умолчанию авто-ретрай выключен (max_tries=1). Идемпотентный per-object лок при
    # этом защищает от конкурентных дублей (двойной клик / двойной enqueue).
    # Восстановление после краша воркера: CRM — повторный запуск по таймауту
    # (STUCK_RECORDING_TIMEOUT_MIN), chat — повторная загрузка пользователем.
    # Поднимать WORKER_MAX_TRIES можно ТОЛЬКО сделав пайплайны идемпотентными.
    max_tries = int(os.getenv("WORKER_MAX_TRIES", "1"))
    keep_result = int(os.getenv("WORKER_KEEP_RESULT", "3600"))
