"""
Веб-сторона очереди задач (arq поверх уже развёрнутого Redis).

Эндпоинты кладут тяжёлые анализы в очередь и сразу отвечают; саму работу
выполняет отдельный worker-сервис (см. app/worker.py).

arq импортируется ЛЕНИВО внутри функций — чтобы при USE_QUEUE=false (фолбэк на
BackgroundTasks) модуль импортировался даже без установленного arq.
"""
import os
import logging
from typing import List, Optional, Any

logger = logging.getLogger("main")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


def use_queue() -> bool:
    """Включена ли очередь (иначе — старое поведение через BackgroundTasks)."""
    return os.getenv("USE_QUEUE", "false").strip().lower() in ("1", "true", "yes", "on")


_arq_pool = None


def _redis_settings():
    from arq.connections import RedisSettings
    return RedisSettings.from_dsn(REDIS_URL)


async def get_arq_pool():
    """Ленивый пул arq для постановки задач (на стороне веба)."""
    global _arq_pool
    if _arq_pool is None:
        from arq import create_pool
        _arq_pool = await create_pool(_redis_settings())
    return _arq_pool


async def close_arq_pool():
    global _arq_pool
    if _arq_pool is not None:
        try:
            await _arq_pool.close()
        except Exception:  # noqa: BLE001
            pass
        _arq_pool = None


# ── Хелперы постановки в очередь ──────────────────────────

async def enqueue_analyze_recording(recording_id: int, owner_id: Optional[int] = None):
    """Один анализ CRM-записи. owner_id — кто инициировал (для per-user fairness)."""
    pool = await get_arq_pool()
    return await pool.enqueue_job("analyze_recording_job", recording_id, owner_id)


async def enqueue_batch(recording_ids: List[int], owner_id: Optional[int] = None) -> List[str]:
    """Батч = N отдельных per-record job (fairness + ретрай по одной записи)."""
    pool = await get_arq_pool()
    job_ids: List[str] = []
    for rid in recording_ids:
        job = await pool.enqueue_job("analyze_recording_job", rid, owner_id)
        if job is not None:
            job_ids.append(job.job_id)
    return job_ids


async def enqueue_run_pipeline(
    kind: str,
    user_id: int,
    conversation_id: int,
    ref: Any,
    progress_conversation_id: Optional[int] = None,
):
    """Chat-анализ (audio/text/raw_text/images и их *_trener-варианты).

    ref: attachment_id (int) | raw_text (str) | список image_attachment_ids (list).
    """
    pool = await get_arq_pool()
    return await pool.enqueue_job(
        "run_pipeline_job", kind, user_id, conversation_id, ref, progress_conversation_id
    )
