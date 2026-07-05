"""Локальный realtime-конвейер голосовых тренировок (STT→LLM→TTS) без Azure.

Собирается из существующих блоков (STTEngine, GPTDialogue, TTSEngine) и заменяет
Azure Voice Live, когда USE_AZURE_VOICE_LIVE=false. Модули:

  audio       — конвертация PCM16⇄float32, base64, линейный ресемплинг (24k⇄16k).
  segmenter   — server-side VAD: нарезка входного потока на реплики (speech_started/ended).
  driver      — оркестратор: реплика → STT → LLM → TTS, эмит protocol-событий фронта.

См. AI_LAYER_RF_MIGRATION_PLAN.md, Итерация 3.
"""
