"""
Адаптер Yandex SpeechKit (v3, асинхронное распознавание с диаризацией) для анализа
звонков. Возвращает ту же структуру, что и ElevenLabs-путь в pipeline.py, поэтому
`_words_to_turns` работает без изменений:

    {"text": <полный текст>,
     "words": [{"speaker_id": <str>, "text": <str>, "start": <sec>, "end": <sec>}, ...]}

Этап 3 миграции AI-слоя (см. AI_LAYER_RF_MIGRATION_PLAN.md).

────────────────────────────────────────────────────────────────────────────────
ВАЖНО (PoC): сетевой контракт SpeechKit v3 (имена полей запроса/ответа) нужно
подтвердить на реальном ключе — держите его в одном месте (константы + parse_*).
Функция parse_recognition() отделена и покрыта юнит-тестом на ВЫХОДНОЙ контракт
(words → _words_to_turns). Если реальные поля ответа отличаются — правится только
_words_from_v3_result(), остальной pipeline не трогается.
────────────────────────────────────────────────────────────────────────────────

ENV:
  YANDEX_API_KEY     API-ключ сервисного аккаунта (роль ai.speechkit-stt.user)
  YANDEX_FOLDER_ID   каталог (нужен некоторым вызовам)
  SPEECHKIT_MODEL    модель распознавания (по умолчанию "general")
  STT_TIMEOUT_SECONDS / STT_MAX_RETRIES  сетевые защиты (переиспользуем из pipeline)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

import httpx

logger = logging.getLogger("main")

# Endpoints SpeechKit v3 (async) + Operation API. Проверить на PoC.
_RECOGNIZE_URL = "https://stt.api.cloud.yandex.net/stt/v3/recognizeFileAsync"
_GET_RECOGNITION_URL = "https://stt.api.cloud.yandex.net/stt/v3/getRecognition"
_OPERATION_URL = "https://operation.api.cloud.yandex.net/operations/"


def _api_key() -> str:
    return os.getenv("YANDEX_API_KEY", "").strip()


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Api-Key {_api_key()}"}


def _model() -> str:
    return os.getenv("SPEECHKIT_MODEL", "general")


# ─── Парсер результата (ВЫХОДНОЙ контракт, покрыт тестом) ──────────────────────

def _words_from_v3_result(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Достаёт слова со спикер-тегами из результата SpeechKit v3 и приводит к
    формату ElevenLabs: [{"speaker_id","text","start","end"}].

    Ожидаемая (документированная) форма фрагмента:
      {"final": {
          "alternatives": [{
              "words": [{"text": "...", "startTimeMs": "120", "endTimeMs": "480"}],
              "text": "...."
          }],
          "channelTag": "1"        # или speakerTag — тег спикера/канала
      }}
    Если реальные имена полей другие — правится ТОЛЬКО эта функция.
    """
    words: List[Dict[str, Any]] = []
    for chunk in _iter_final_chunks(payload):
        speaker = str(
            chunk.get("speakerTag")
            or chunk.get("channelTag")
            or chunk.get("speaker_tag")
            or "1"
        )
        alts = chunk.get("alternatives") or []
        if not alts:
            continue
        for w in (alts[0].get("words") or []):
            text = (w.get("text") or "").strip()
            if not text:
                continue
            words.append({
                "speaker_id": f"speaker_{speaker}",
                "text": text,
                "start": _ms_to_sec(w.get("startTimeMs") or w.get("start_time_ms") or 0),
                "end": _ms_to_sec(w.get("endTimeMs") or w.get("end_time_ms") or 0),
            })
    return words


def _iter_final_chunks(payload: Dict[str, Any]):
    """Возвращает финальные фрагменты распознавания из разных возможных обёрток
    ответа v3 (result/response/chunks/список). Толерантна к структуре."""
    # getRecognition отдаёт последовательность объектов; на входе может быть
    # либо один объект, либо {"result":[...]} / {"chunks":[...]} / список.
    if isinstance(payload, list):
        items = payload
    else:
        items = (
            payload.get("result")
            or payload.get("chunks")
            or payload.get("response")
            or [payload]
        )
    for item in items:
        if not isinstance(item, dict):
            continue
        # интересуют только финальные результаты (не partial).
        # ПОДТВЕРЖДЕНО НА PoC: v3 шлёт "final" и следом отдельной строкой
        # "finalRefinement" (тот же сегмент, только normalizedText с пунктуацией) —
        # если брать оба, слова задваиваются. Слова идентичны в обоих, поэтому
        # берём только "final"; "finalRefinement" пропускаем как дубликат.
        final = item.get("final")
        if isinstance(final, dict):
            yield final
        elif "alternatives" in item:
            yield item


def _ms_to_sec(v: Any) -> float:
    try:
        return round(int(str(v).replace("s", "").strip() or 0) / 1000.0, 2)
    except (ValueError, TypeError):
        return 0.0


def parse_recognition(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Нормализует ответ SpeechKit в {"text","words"} (контракт pipeline)."""
    words = _words_from_v3_result(payload)
    text = " ".join(w["text"] for w in words).strip()
    return {"text": text, "words": words}


# ─── Сетевой поток (async v3) — проверить на PoC ──────────────────────────────

async def transcribe(wav_path: Path) -> Dict[str, Any]:
    """Распознаёт WAV через SpeechKit v3 (async, диаризация) и возвращает
    {"text","words"} в формате, совместимом с _words_to_turns."""
    if not _api_key():
        raise RuntimeError("YANDEX_API_KEY не задан для SpeechKit STT")

    timeout = httpx.Timeout(float(os.getenv("STT_TIMEOUT_SECONDS", "120")))
    with open(wav_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("ascii")

    request_body = {
        "content": content_b64,
        "recognitionModel": {
            "model": _model(),
            "audioFormat": {"containerAudio": {"containerAudioType": "WAV"}},
            "textNormalization": {
                "textNormalization": "TEXT_NORMALIZATION_ENABLED",
                "literatureText": True,
            },
        },
        # Диаризация (разметка говорящих) — ключевое требование анализа звонков.
        "speakerLabeling": {"speakerLabeling": "SPEAKER_LABELING_ENABLED"},
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        # 1) Старт асинхронной операции
        resp = await client.post(_RECOGNIZE_URL, headers=_headers(), json=request_body)
        resp.raise_for_status()
        op_id = resp.json().get("id")
        if not op_id:
            raise RuntimeError(f"SpeechKit: не получен operation id: {resp.text[:200]}")

        # 2) Ожидание завершения операции
        await _wait_operation(client, op_id)

        # 3) Забор результата распознавания
        res = await client.get(
            _GET_RECOGNITION_URL, headers=_headers(), params={"operationId": op_id}
        )
        res.raise_for_status()
        payload = _load_jsonl(res.text)

    return parse_recognition(payload)


async def _wait_operation(client: httpx.AsyncClient, op_id: str) -> None:
    """Поллинг Operation API до done=true (с бэкоффом)."""
    max_polls = int(os.getenv("SPEECHKIT_MAX_POLLS", "120"))
    for i in range(max_polls):
        r = await client.get(_OPERATION_URL + op_id, headers=_headers())
        r.raise_for_status()
        data = r.json()
        if data.get("done"):
            if "error" in data and data["error"]:
                raise RuntimeError(f"SpeechKit operation error: {data['error']}")
            return
        await asyncio.sleep(min(2 + i * 0.5, 10))
    raise TimeoutError("SpeechKit: операция не завершилась за отведённое время")


def _load_jsonl(text: str) -> Dict[str, Any]:
    """getRecognition возвращает поток JSON-объектов (по одному на строку), каждый
    обёрнут в {"result": {...}} (ПОДТВЕРЖДЕНО НА PoC). Снимаем эту обёртку, чтобы
    получить чанки вида {"final"/"finalRefinement"/"eouUpdate": ..., "channelTag": ...}
    — именно этот плоский вид ожидает _words_from_v3_result()."""
    import json

    items: List[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        items.append(parsed.get("result", parsed) if isinstance(parsed, dict) else parsed)
    # частый случай: единый JSON целиком
    if not items:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
    return {"result": items}
