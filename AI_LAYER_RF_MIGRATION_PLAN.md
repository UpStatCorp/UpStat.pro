# План замены AI-слоя UpStat.pro для развёртывания в РФ

**Версия:** 1.0 · **Дата:** 28.06.2026 · **Статус:** рабочий технический план
**Контекст:** OpenAI / Azure / ElevenLabs ограничивают РФ. Нужно перевести весь AI-слой
на российские облачные сервисы (YandexGPT/GigaChat, SpeechKit/SaluteSpeech) или на
self-host (vLLM + faster-whisper), не ломая существующую логику.

> Все ссылки на код приведены в формате `файл:строка` и актуальны на момент составления
> плана. Перед каждым этапом сверяйтесь с приложением «Инвентарь AI-слоя» (раздел 15).

---

## 1. Цель и принципы

1. **Один код — две конфигурации.** Тот же репозиторий должен собираться и под КЗ
   (OpenAI), и под РФ (российские/self-host провайдеры) только сменой `.env`.
   Никаких форков.
2. **Провайдер выбирается через переменные окружения**, а не правкой кода.
3. **Минимум вмешательства в бизнес-логику.** Код уже использует клиент `openai`
   с интерфейсом `chat.completions`. Большинство российских/self-host вариантов
   дают OpenAI-совместимый API — значит, основная работа сводится к подмене
   `base_url` + имени модели.
4. **Данные не должны утекать за периметр.** При self-host аудио и текст не покидают
   сервер в РФ. При облачных API РФ — передаётся только то, что разрешено (см. раздел 10).

---

## 2. Что сейчас и почему ломается

| Подсистема | Где в коде | Модель/сервис сейчас | Проблема в РФ |
|---|---|---|---|
| Анализ звонка (LLM) | `app/services/pipeline.py` | OpenAI `gpt-4o` | блок |
| Анализ для тренера | `app/services/pipeline_trener.py` | OpenAI `gpt-4o`; STT — только ElevenLabs (`whisper-1` тут **отключён**, см. §15.2) | блок |
| Извлечение параметров | `app/services/parameter_extraction.py` | OpenAI `gpt-4o` | блок |
| Ассистент аналитики | `app/services/analytics_assistant.py` | OpenAI `gpt-4o` | блок |
| SQL/аналитика-ассистент | `app/services/analytics_queries.py` | OpenAI `gpt-4o-mini` | блок |
| Действия менеджера | `app/services/manager_actions_service.py` | OpenAI `gpt-4o-mini` | блок |
| Валидатор тренировок | `app/services/training_validator_service.py` | `gpt-4o-mini` | блок |
| План тренировок | `app/services/training_plan_service.py` | `gpt-4o-mini` | блок |
| Скрипты команды | `app/services/team_script_service.py` | `gpt-4o-mini` | блок |
| Паспорт продавца | `app/services/seller_passport_service.py` | `gpt-4o-mini` | блок |
| Анализ изображений (vision) | `app/services/image_pipeline.py` | OpenAI `gpt-4o` (vision) | блок + не у всех РФ-LLM есть vision |
| Research-режим | `app/services/research_service.py` | OpenAI `gpt-4o` | блок |
| STT (анализ звонков) | `pipeline.py` `_elevenlabs_transcribe` / `_openai_whisper_transcribe` | ElevenLabs (диаризация) + Whisper API | блок |
| STT (голос-ассистент) | `voice_assistant/stt_reactive.py` | whisper_openai / whisper_local / elevenlabs | блок (кроме whisper_local) |
| TTS (голос-ассистент) | `voice_assistant/tts_response.py` | OpenAI TTS / ElevenLabs | блок |
| Realtime-голос | `voice_assistant/azure_voice_live.py`, `app/services/training_stages_service.py` | Azure Voice Live (`gpt-4o-realtime`) | блок |

**Важная деталь по данным:** PII-редактор (`app/services/pii_redactor.py`) **уже** применяется к
тексту перед LLM-анализом (`pipeline.py:930,1028,1212`, `pipeline_trener.py:279`) — телефоны,
ФИО, e-mail, ИНН и т.п. заменяются на плейсхолдеры `[PHONE]`, `[PERSON]` и др.
**НО** редактирование идёт по тексту **после** распознавания; в STT (ElevenLabs/Whisper)
аудио уходит **сырым**. Это ключевой аргумент в пользу self-host STT.

---

## 3. Целевая архитектура: три пути

| Путь | LLM | STT | TTS | Realtime | GPU | Когда выбирать |
|---|---|---|---|---|---|---|
| **A. Self-host (рекомендуется)** | vLLM + Qwen2.5/Llama (OpenAI-совм.) | faster-whisper (+ диаризация) | SaluteSpeech/SpeechKit | свой конвейер STT→LLM→TTS | да | независимость от санкций, данные не покидают РФ |
| **B. Облако РФ** | YandexGPT (OpenAI-совм.) или GigaChat | Yandex SpeechKit / SaluteSpeech | те же | свой конвейер на облачных STT/LLM/TTS | нет | быстрый старт, не хочется держать GPU |
| **C. Гибрид** | self-host LLM + облачные STT/TTS | SpeechKit | SaluteSpeech | смешанный | да (только LLM) | баланс цены/контроля |

**Рекомендация:** начать с **Пути B** (быстрее запустить, проверить качество), а
тяжёлый анализ звонков и realtime при росте нагрузки перенести на **Путь A**.
Архитектура ниже делает переключение вопросом `.env`, поэтому смена пути не требует
переписывания.

---

## 4. Базовая абстракция: единый слой провайдера (фундамент всех этапов)

Сейчас клиент OpenAI создаётся **в 15 местах независимо** (11 в `app/services`
+ 4 в `voice_assistant`), плюс есть **2 клиента ElevenLabs**; имя модели зашито
в ~25 местах. Это и есть корень проблемы. Сначала вводим единую точку конфигурации.

### 4.1. Новый модуль `app/services/ai_provider.py`

Назначение — фабрика клиентов и резолвер моделей. Псевдо-API:

```python
# Конфиг из ENV
OPENAI_BASE_URL   = os.getenv("OPENAI_BASE_URL")  # None => api.openai.com
LLM_MODEL_MAIN    = os.getenv("LLM_MODEL_MAIN", "gpt-4o")
LLM_MODEL_MINI    = os.getenv("LLM_MODEL_MINI", "gpt-4o-mini")
LLM_MODEL_VISION  = os.getenv("LLM_MODEL_VISION", "gpt-4o")
LLM_PROVIDER      = os.getenv("LLM_PROVIDER", "openai")  # openai|yandex|gigachat|vllm

def get_llm_client() -> OpenAI: ...        # sync, с base_url/timeout/retries
def get_async_llm_client() -> AsyncOpenAI: ...
def model_main() -> str: ...               # вернёт LLM_MODEL_MAIN
def model_mini() -> str: ...
```

Для провайдеров с не-OpenAI-авторизацией (GigaChat) этот модуль инкапсулирует
получение токена и подстановку `base_url`/заголовков, чтобы остальной код не знал
деталей.

### 4.2. Новые переменные окружения (добавить в `.env.example` и `env.example`)

```
LLM_PROVIDER=openai            # openai | yandex | gigachat | vllm
OPENAI_BASE_URL=               # напр. http://gpu-host:8000/v1 (vLLM) или Yandex endpoint
LLM_MODEL_MAIN=gpt-4o
LLM_MODEL_MINI=gpt-4o-mini
LLM_MODEL_VISION=gpt-4o
STT_PROVIDER=whisper_openai    # whisper_openai | whisper_local | elevenlabs | speechkit | salute
TTS_PROVIDER=elevenlabs        # elevenlabs | openai | speechkit | salute
# Yandex Cloud
YANDEX_API_KEY=
YANDEX_FOLDER_ID=
# GigaChat (Sber)
GIGACHAT_CLIENT_ID=
GIGACHAT_CLIENT_SECRET=
GIGACHAT_SCOPE=GIGACHAT_API_PERS
```

> Существующие `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `STT_TIMEOUT_SECONDS`,
> `STT_MAX_RETRIES`, `OPENAI_VALIDATOR_MODEL`, `WHISPER_MODEL/DEVICE/COMPUTE_TYPE`
> уже есть — переиспользуем, не дублируем.

---

## 5. Этап 1 — Параметризация (рефакторинг без смены провайдера)

**Цель:** вынести `base_url` и имена моделей в конфиг. После этого проект на OpenAI
работает ровно как сейчас, но готов к подмене провайдера. **Низкий риск, делаем первым.**

### 5.1. Заменить создание клиента на `get_llm_client()` — 11 мест в `app/services`

`pipeline.py:53`, `pipeline_trener.py:31`, `analytics_queries.py:27`,
`analytics_assistant.py:30`, `parameter_extraction.py:27`,
`manager_actions_service.py:41`, `team_script_service.py:14`,
`seller_passport_service.py:49`, `image_pipeline.py:29`,
`training_validator_service.py:45`, `training_plan_service.py:86`.

Ещё **4 клиента в `voice_assistant`** (`gpt_logic.py:36`, `stt_reactive.py:23`,
`tts_response.py:67,81,98`) и **2 клиента ElevenLabs** (`tts_response.py:89`,
`stt_reactive.py:32`) переводятся на фабрику в рамках этапов 3–5 (STT/TTS/realtime),
т.к. они часть голосового конвейера. Полный список — в §15.1.

Каждый `OpenAI(api_key=...)` / `AsyncOpenAI(api_key=...)` → вызов фабрики
(она внутри добавит `base_url=OPENAI_BASE_URL`, существующие `timeout`/`max_retries`).

### 5.2. Заменить захардкоженные модели на резолверы

`"gpt-4o"` → `model_main()`, `"gpt-4o-mini"` → `model_mini()`,
vision-вызов в `image_pipeline.py:127` → `model_vision()`.

Полный список мест — в разделе 12. Также `voice_assistant/config.py:20`
(`GPT_MODEL`) уже параметризован — привести к тем же именам переменных.

### 5.3. Критерий готовности этапа 1

- Проект собирается, тесты (`tests/`) проходят на OpenAI без изменения поведения.
- Греп `grep -rn '"gpt-4o' app/services` не находит **захардкоженных** моделей
  в вызовах (кроме комментариев).

---

## 6. Этап 2 — Замена LLM

### 6.1. Вариант B1 — YandexGPT (OpenAI-совместимый режим) — ✅ ОБВЯЗКА РЕАЛИЗОВАНА

**Код готов** (`services/ai_provider.py`): в режиме `LLM_PROVIDER=yandex` автоматически
подставляются endpoint `https://llm.api.cloud.yandex.net/v1`, ключ из `YANDEX_API_KEY`
и имя модели `gpt://<YANDEX_FOLDER_ID>/yandexgpt(-lite)/latest` (алиасы `gpt-4o`→
`yandexgpt`, `gpt-4o-mini`→`yandexgpt-lite`; полный `gpt://…` пропускается как есть).
STT-Whisper отделён (`get_stt_client`) и на YandexGPT не переключается.

**Остаётся сделать (нужны доступы заказчика):**
1. Yandex Cloud (`cloud.yandex.ru`): платёжный аккаунт, каталог (folder), сервисный
   аккаунт с ролью **`ai.languageModels.user`**, создать **API-ключ**.
2. В `.env`: `LLM_PROVIDER=yandex`, `YANDEX_API_KEY=...`, `YANDEX_FOLDER_ID=...`.
3. PoC: `PYTHONPATH=app python scripts/poc_llm.py` — проверить связь, JSON-ответ и
   поддержку `response_format`.
4. **Перепроверить промпты** (таблица `prompts` + `SYSTEM_AUDITOR` в `pipeline.py`):
   разбор скоринга `_extract_and_collect_scores` протестировать на реальных звонках.

**Известные ограничения YandexGPT — ✅ ПРЕДОБРАБОТАНЫ В КОДЕ (env-флаги auto|on|off):**
Все три кавета закрыты в `services/ai_provider.py` так, что один код работает и под
OpenAI (КЗ), и под YandexGPT (РФ); после PoC любой квет чинится сменой ОДНОЙ
переменной, без правок кода. Дефолт `auto` = поведение по провайдеру.
- **vision не поддерживается** этим endpoint → `vision_enabled()` + `VISION_ENABLED`
  (auto: OpenAI=вкл, YandexGPT=выкл). В yandex-режиме `image_pipeline` кидает
  `VisionUnavailableError`, пользователь видит понятное сообщение вместо ошибки API
  (см. §6.4). KZ/OpenAI работает как прежде.
- лимит `max_tokens` ниже, чем у gpt-4o → `clamp_max_tokens()` + `LLM_MAX_OUTPUT_TOKENS`
  (auto: OpenAI без потолка, YandexGPT=8000). Применён к research-вызовам
  `pipeline.py` (12000/8000), `parameter_extraction.py` (16000), `seller_passport`/
  `manager_actions` (8000). Для OpenAI — no-op.
- `response_format={"type":"json_object"}` → `json_mode_kwargs()` + `LLM_JSON_MODE`
  (auto=вкл). При `LLM_JSON_MODE=off` параметр не отправляется, а ответ парсится
  устойчиво через `extract_json()` (снимает ```json-ограждение и обрезает до
  сбалансированного объекта). Покрыто `tests/test_ai_provider_caveats.py`.
  PoC-скрипт по-прежнему детектирует поддержку — если YandexGPT её примет, ничего
  переключать не нужно.

### 6.2. Вариант B2 — GigaChat (Sber)

1. `developers.sber.ru` → продукт **GigaChat** → получить `Client ID` / `Client Secret`,
   `scope`.
2. Авторизация: получить OAuth-токен (живёт ~30 мин) → класть в заголовок.
   Это инкапсулировать в `ai_provider.py` (рефреш токена).
3. У GigaChat свой REST-формат (не везде 100% OpenAI-совместим) — возможно, нужен
   тонкий адаптер поверх `chat.completions`-вызовов.

### 6.3. Вариант A — self-host vLLM (максимальная независимость)

1. GPU-сервер в РФ (см. инфраструктурный отчёт): 1×A100 40GB или 2×A10/RTX 4090.
2. Поднять **vLLM** (`docs.vllm.ai`) с открытой моделью (Qwen2.5-32B-Instruct AWQ или
   Llama-3.x) — vLLM отдаёт **OpenAI-совместимый** `/v1`.
3. `.env`: `LLM_PROVIDER=vllm`, `OPENAI_BASE_URL=http://gpu-host:8000/v1`,
   `LLM_MODEL_MAIN=<имя модели в vLLM>`, `OPENAI_API_KEY=<любой токен vLLM>`.
4. Код почти не меняется (тот же клиент, другой `base_url`).

### 6.4. Отдельно — vision (`image_pipeline.py`) — ✅ ФЛАГ РЕАЛИЗОВАН

`gpt-4o` используется для анализа изображений (vision). Не у всех РФ/self-host LLM
есть vision. **Реализовано:** флаг `VISION_ENABLED` (auto|on|off) + `vision_enabled()`
в `ai_provider.py`. Дефолт `auto`: OpenAI/vLLM = вкл, YandexGPT = выкл. При
выключенном vision `extract_dialogue_from_images` кидает `VisionUnavailableError`,
а оба пайплайна (основной и тренерский) ловят её и показывают пользователю понятное
«❌ Анализ скриншотов недоступен…» вместо сырой ошибки API.
Дальнейшие варианты, когда vision нужен под РФ: GigaChat-Vision / self-host VLM
(Qwen2.5-VL) — тогда `VISION_ENABLED=on` + отдельный vision-клиент/провайдер.

### 6.5. Критерий готовности этапа 2

End-to-end: загрузка звонка → распознавание → анализ → корректный JSON-отчёт и
скоринг на новой LLM. Качество промптов подтверждено на 5–10 реальных звонках.

---

## 7. Этап 3 — Замена STT (распознавание речи)

Два разных сценария — разные требования:

### 7.1. Анализ звонков (нужна диаризация спикеров) — ✅ ВЫБРАН Yandex SpeechKit, ШОВ РЕАЛИЗОВАН

**Решение:** Yandex SpeechKit (облако, в связке с YandexGPT).

**Что реализовано в коде:**
- Провайдер-шов `CALL_STT_PROVIDER` (`elevenlabs` по умолчанию — поведение не меняется;
  `speechkit` — Yandex) + диспетчер `_transcribe_primary` в `pipeline.py`.
- Адаптер `app/services/speechkit_stt.py`: `transcribe()` (async v3 + диаризация) и
  `parse_recognition()` → тот же формат `{"text","words":[{speaker_id,text,start,end}]}`,
  что и ElevenLabs, поэтому `_words_to_turns` **не менялся**.
- Тест `tests/test_speechkit_stt.py` фиксирует ВЫХОДНОЙ контракт (words → диалог
  2 спикеров) — 4 теста зелёные.

**Остаётся (нужен ключ — PoC):**
1. Yandex Cloud: сервисный аккаунт с ролью **`ai.speechkit-stt.user`**, тот же
   `YANDEX_API_KEY`/`YANDEX_FOLDER_ID`.
2. `.env`: `CALL_STT_PROVIDER=speechkit`.
3. **Подтвердить ВХОДНОЙ контракт** ответа SpeechKit v3 на реальном звонке: если
   имена полей отличаются от ожидаемых — правится ТОЛЬКО `_words_from_v3_result()`
   (endpoints/поля собраны в константах `speechkit_stt.py`), остальной pipeline не
   трогается. Ключевое к проверке: формат `getRecognition` и тег спикера
   (`speakerTag`/`channelTag`).

Точки изменений (сделаны): `pipeline.py` — `CALL_STT_PROVIDER`, `_transcribe_primary`,
переключение вызова; новый `speechkit_stt.py`. `pipeline_trener.py` STT — отдельно
(там whisper мёртв, основной STT тоже ElevenLabs → аналогичный шов при необходимости).

### 7.2. Голосовой ассистент / тренировки (диаризация не нужна)

В `voice_assistant/stt_reactive.py` уже есть провайдер **`whisper_local`** (faster-whisper,
параметры `WHISPER_MODEL/DEVICE/COMPUTE_TYPE` в `config.py:42-44`). Для РФ достаточно
выставить `STT_PROVIDER=whisper_local` (или добавить `speechkit`). Самый дешёвый по
переделке участок.

### 7.3. Критерий готовности

Транскрипт звонка с корректными `speaker_1/speaker_2` и таймкодами на новом STT;
голосовой ассистент распознаёт реплики локально без OpenAI/ElevenLabs.

---

## 8. Этап 4 — Замена TTS (синтез речи) — ✅ SpeechKit РЕАЛИЗОВАН

**Что реализовано** в `voice_assistant/tts_response.py`:
- Провайдер **`speechkit`** в `TTSEngine` (`__init__` + `_synthesize` + метод
  `_synthesize_speechkit`). Использует SpeechKit TTS v1, `format=lpcm` 16 кГц —
  LPCM конвертируется в numpy float32 **без ffmpeg** (хелпер `_pcm16_to_float32`),
  выходной контракт (чанки float32 при `SAMPLE_RATE`) тот же, что у openai/elevenlabs.
- Тест `tests/test_speechkit_tts.py` (конвертация/нормализация/тишина) — зелёный.
- `.env`: `TTS_PROVIDER=speechkit`, `SPEECHKIT_TTS_VOICE` (alena/filipp/…),
  ключ — общий `YANDEX_API_KEY` (роль `ai.speechkit-tts.user`).

**Остаётся (PoC с ключом):** подобрать голос/эмоцию, проверить качество и латентность;
при необходимости добавить `salute` (SaluteSpeech) по той же схеме. Проверить
`app/routers/tts_proxy.py`, чтобы прокси не ходил в зарубежный TTS.

### 8.1. Критерий готовности

Ответы ассистента озвучиваются российским TTS; качество/тональность приемлемы.

---

## 9. Этап 5 — Realtime-голосовые тренировки — ✅ ЛОКАЛЬНЫЙ ДРАЙВЕР РЕАЛИЗОВАН (v1)

Прямого аналога Azure Voice Live (единый канал речь↔модель) в РФ нет — оркестрацию
(VAD, детекция реплик, склейка STT→LLM→TTS) собрали сами. Раньше боевой обработчик
`voice_assistant/websocket_handler.py` при `USE_AZURE_VOICE_LIVE=false` **закрывал
WebSocket** (`code=1008`). Теперь эта ветка делегирует в локальный драйвер.

**Реализовано** — пакет `voice_assistant/realtime/` (half-duplex v1):
- `audio.py` — конвертация провода `PCM16@24k ⇄ float32@16k` + линейный ресемплинг
  (тот же контракт, что у Azure/фронта → `voice-training.js` не меняли).
- `segmenter.py` — **server-side VAD** (`UtteranceSegmenter`): дебаунс старта +
  hangover тишины + max/min длина, классификатор кадра сменный (по умолч. энергия RMS).
- `driver.py` — оркестратор `LocalRealtimeDriver`: реплика → STT → LLM → TTS, эмит тех
  же protocol-событий (`speech_started`/`user_text`/`status`/`ai_text`/`response.audio.*`).
  Half-duplex: пока говорит ИИ — микрофон игнорируется (barge-in — отдельный шаг).
- `handler.py` — FastAPI-обвязка: сессия/capacity/БД/этапы/промпт, стартовая реплика
  ИИ, цикл сообщений; многоэтапные тренировки — через теги `[STAGE_COMPLETE]`/
  `[TRAINING_COMPLETE]` + локальный переход этапа (без function-calling).
- Ветка в `websocket_handler.py`: `USE_AZURE_VOICE_LIVE=false` → `handle_local_realtime_connection`.

STT = `whisper_local` (офлайн, шарится между сессиями) / `whisper_openai`; LLM = общий
`LLM_PROVIDER` (yandex); TTS = `speechkit`. Пороги VAD — через `LOCAL_VAD_*`.

**Покрытие тестами** (без ключей/сети): `tests/test_realtime_audio.py`,
`test_realtime_segmenter.py`, `test_realtime_driver.py` — аудио-контракт, границы
реплик, полная последовательность событий турна, half-duplex, теги этапов, ошибки.

**Хардеринг после независимого ревью (исправлено):**
- Турн уходит в **фоновую задачу** — чтение сокета не блокируется (пинги отвечаются),
  плюс пост-реплика `cooldown` (`LOCAL_TURN_COOLDOWN_MS`), чтобы «хвост» эха/буфер
  транспорта не распознавался как новая реплика (half-duplex теперь честный).
- Ответ ИИ уходит в БД **редактированным** (`redact_pii`), не только на клиент (152-ФЗ).
- После `response.audio.done` шлём терминальный `status: completed` — фронт корректно
  сбрасывает `activeResponseId`/микрофон (не через barge-in fallback).
- STT-модель грузится **в потоке** (`asyncio.to_thread`), не блокируя event loop.
- Фоновые kickoff/турны трекаются и **отменяются** в `driver.close()`.
- `LOCAL_VAD_MIN_UTTER_MS` поднят до 500 мс (STT всё равно отбрасывает <0.5 c).

**Осталось (нужны ключи / прод):**
1. E2E-прогон полной тренировки на цепочке без Azure (SpeechKit/YandexGPT либо
   whisper_local офлайн) + замер задержки реплики.
2. Тюнинг порогов VAD (`LOCAL_VAD_*`) под реальный микрофон/шум.
3. Нагрузочный тест: одна общая faster-whisper модель под 100+ сессий
   (CTranslate2 thread-safe, но сериализует — проверить пропускную способность).
4. При необходимости — barge-in (v2): фронт уже умеет (есть cancelMsg), драйвер имеет
   `tts.request_stop()`; включить прерывание TTS при детекте речи во время ответа.
5. Оптимизация задержки: стриминг LLM→TTS по предложениям (сейчас v1 копит полный
   ответ ради надёжности), `GPT_MAX_TOKENS=150` уже стоит.

### 9.1. Критерий готовности

Полная голосовая тренировка проходит на цепочке без Azure; задержка реплики приемлема
(целевая — ≤2–3 c на ответ). Ядро (audio/VAD/driver) — под юнит-тестами; e2e — с ключами.

---

## 10. Этап 6 — Данные и безопасность AI-слоя

- **Текст:** PII уже редактируется до LLM (`redact_pii`). При облачных РФ-LLM это
  снижает риск; при self-host — текст не покидает периметр вовсе.
- **Аудио в STT:** сейчас уходит сырым. При облачном SpeechKit/SaluteSpeech — это
  передача ПД (записи с голосом). При self-host faster-whisper — аудио остаётся в РФ.
  Для чистого соответствия 152-ФЗ предпочтителен self-host STT либо договор обработки
  с провайдером.
- **Логи:** ✅ закрыто флагом `LOG_AI_CONTENT` (по умолчанию **false**). Гейтит
  логи с сырым контентом: `gpt_logic.py` (реплики пользователя/ассистента, ответ GPT
  — через `_snip`), `stt_reactive.py` (распознанный сегмент), `websocket_handler.py`
  (распознанная речь). По умолчанию в логи/Sentry идёт только длина, без контента.
  Sentry уже `send_default_pii=False`, события только на ERROR (`logging_config.py`).
  Основной пайплайн (`pipeline.py`/`pipeline_trener.py`) сырой транскрипт/ответ в логи
  не пишет (проверено). Осталось: `research`-режим сохраняет prompt/ответ в файлы/БД —
  это фича, для РФ хранить в РФ и ограничить доступ/срок (данные-локальность, не лог).
- **Ключи:** для РФ-инстанса отдельный `.env`; не переиспользовать ключи КЗ/OpenAI.

---

## 11. Порядок работ: 3 итерации

Шесть этапов (разделы 5–10) укладываются в **три итерации**. Каждая итерация
заканчивается работающим, проверяемым состоянием продукта — можно останавливаться
после любой и иметь пригодный результат.

```
Итерация 1: Этап 1 + Этап 2          →  анализ звонков (текст) на РФ-LLM
Итерация 2: Этап 3 + Этап 4 + Этап 6 →  полный анализ без OpenAI/ElevenLabs
Итерация 3: Этап 5 + e2e             →  realtime-тренировки без Azure, запуск
```

### Итерация 1 — «Мозг» (этапы 1–2, ~1 неделя)

**Скоуп:** `ai_provider.py` + параметризация 11 клиентов в `app/services` и всех
захардкоженных моделей (§15.1–15.2) → переключение LLM на YandexGPT (или vLLM)
через `.env` → PoC промптов на 5–10 реальных звонках.

**Готово, когда:** транскрипт (пока старым STT или готовый текст) прогоняется через
РФ-LLM, JSON-отчёт и скоринг корректны, `tests/` зелёные на OpenAI-конфиге
(обратная совместимость не сломана).

### Итерация 2 — «Уши и голос» (этапы 3, 4, 6, ~1–1.5 недели)

**Скоуп:** STT с диаризацией (SpeechKit / whisper+WhisperX) с адаптацией
`_words_to_turns` → TTS на SpeechKit/Salute в `TTSEngine` → перевод 4 клиентов
`voice_assistant` и 2 клиентов ElevenLabs на фабрику → зачистка логов/ключей (этап 6).

**Готово, когда:** звонок проходит цикл загрузка → распознавание → анализ → отчёт
**без единого вызова** OpenAI/ElevenLabs/Azure; диаризация подтверждена на 10 звонках.
На этой точке основной продукт (анализ звонков) полностью пригоден для РФ.

### Итерация 3 — «Realtime» (этап 5, ~1–2 недели)

**Скоуп:** драйвер-цикл STT→LLM→TTS в `websocket_handler.py` (сейчас при
`USE_AZURE_VOICE_LIVE=false` он закрывает сокет — см. раздел 9) → тюнинг задержки
(стриминг LLM, chunked-TTS, VAD) → полный e2e-прогон и нагрузочный тест очереди.

**Готово, когда:** голосовая тренировка проходит целиком без Azure, задержка ≤2–3 с.

| Итерация | Этапы | Риск | Оценка |
|---|---|---|---|
| 1. Мозг (LLM) | 1, 2 | низкий–средний (промпты) | ~1 нед. |
| 2. Уши и голос (STT/TTS) | 3, 4, 6 | **высокий** (диаризация) | ~1–1.5 нед. |
| 3. Realtime | 5 | высокий (latency, новый код) | ~1–2 нед. |

Если нужно ужаться в **2 итерации**: слить 1+2 (одна большая итерация «весь
не-realtime AI»), realtime оставить второй. Не рекомендуется резать наоборот —
диаризация (итерация 2) слишком рискованна, чтобы смешивать её с realtime.

---

## 12. Тест-план

1. **Юнит/смоук:** прогон `tests/` после этапа 1 (поведение не изменилось).
2. **Контракт LLM:** на 10 реальных (обезличенных) звонках сравнить JSON-отчёты
   старой (gpt-4o) и новой LLM — поля, скоринг, отсутствие отказов модели.
3. **STT:** сверить диаризацию (доля корректно разделённых реплик) на 10 звонках.
4. **TTS:** субъективная оценка разборчивости/тональности.
5. **Realtime:** замер задержки реплики, прохождение полной тренировки.
6. **Нагрузка:** очередь arq на N параллельных анализов (см. `QUEUE_SCALING.md`).

---

## 13. Риски и открытые вопросы

- **Диаризация в РФ-STT.** Главный технический риск этапа 3 — качество разметки
  спикеров у SpeechKit/SaluteSpeech vs ElevenLabs. Нужен PoC до полной интеграции.
- **Качество промптов на РФ-LLM.** gpt-4o-промпты могут давать другой формат/отказы
  на YandexGPT/GigaChat. Заложить итерацию по промптам.
- **Vision.** Доступность мультимодальности у выбранного провайдера (раздел 6.4).
- **Latency realtime** без Azure — нужно подтвердить приемлемость UX.
- **Стоимость GPU** при self-host (раздел инфраструктурного отчёта).

---

## 14. Чек-лист готовности AI-слоя

- [ ] Создан `ai_provider.py`, добавлены ENV-переменные в оба `*.example`.
- [ ] Все 15 мест создания OpenAI-клиента (11 `app/services` + 4 `voice_assistant`) используют фабрику; 2 клиента ElevenLabs заменены.
- [ ] Все захардкоженные модели заменены на `model_main()/model_mini()/model_vision()`.
- [ ] Тесты проходят на OpenAI без изменения поведения (этап 1 завершён).
- [ ] LLM переключается на Yandex/GigaChat/vLLM через `.env`.
- [ ] Промпты перепроверены, JSON-скоринг разбирается корректно.
- [ ] STT для анализа звонков даёт корректную диаризацию на новом провайдере.
- [ ] STT голос-ассистента работает на `whisper_local` (или SpeechKit).
- [ ] TTS переведён на SpeechKit/SaluteSpeech.
- [ ] В `websocket_handler.py` написан драйвер realtime для `USE_AZURE_VOICE_LIVE=false` (сейчас он закрывает сокет); тренировка проходит на цепочке STT→LLM→TTS без Azure.
- [ ] Vision решён (заменён или временно отключён).
- [ ] Аудио STT не покидает РФ (self-host) либо оформлен договор обработки.
- [ ] Отдельный `.env` для РФ, ключи КЗ/OpenAI не переиспользуются.
- [ ] End-to-end сценарий пройден на реальном звонке.

---

## 15. Приложение: полный инвентарь AI-слоя

### 15.1. Создание клиента OpenAI (заменить на фабрику)

| Файл:строка | Тип |
|---|---|
| `app/services/pipeline.py:53` | `OpenAI` |
| `app/services/pipeline_trener.py:31` | `OpenAI` |
| `app/services/analytics_queries.py:27` | `OpenAI` |
| `app/services/analytics_assistant.py:30` | `OpenAI` |
| `app/services/parameter_extraction.py:27` | `OpenAI` |
| `app/services/manager_actions_service.py:41` | `AsyncOpenAI` |
| `app/services/team_script_service.py:14` | `OpenAI` |
| `app/services/seller_passport_service.py:49` | `AsyncOpenAI` |
| `app/services/image_pipeline.py:29` | `OpenAI` |
| `app/services/training_validator_service.py:45` | `AsyncOpenAI` |
| `app/services/training_plan_service.py:86` | `AsyncOpenAI` |
| `voice_assistant/gpt_logic.py:36` | `AsyncOpenAI` (диалог голос-ассистента) |
| `voice_assistant/stt_reactive.py:23` | `OpenAI` (Whisper API STT) |
| `voice_assistant/tts_response.py:67,81,98` | `AsyncOpenAI` (TTS, 3 точки) |

**Итого OpenAI-SDK клиентов: 15** (11 в `app/services` + 4 в `voice_assistant`).
Плюс **2 клиента ElevenLabs**, которые тоже нужно заменить:
`voice_assistant/tts_response.py:89` (TTS) и `voice_assistant/stt_reactive.py:32` (STT).

### 15.2. Захардкоженные модели (заменить на резолверы)

**`gpt-4o`** → `model_main()`:
`pipeline.py:144,432,439,462,688,696,712` ·
`pipeline_trener.py:373,416,544,582,767,810` ·
`parameter_extraction.py:147,193` · `analytics_assistant.py:408` ·
`research_service.py:562` · `image_pipeline.py:127` (vision → `model_vision()`).

**`gpt-4o-mini`** → `model_mini()`:
`manager_actions_service.py:108,167` · `analytics_queries.py:345` ·
`training_validator_service.py:28` · `training_plan_service.py:163` ·
`team_script_service.py:135` · `seller_passport_service.py:118,169`.

**`whisper-1`** (STT) → провайдер STT:
`pipeline.py:241` (живой фолбэк) · `voice_assistant/stt_reactive.py:393` (живой вызов).
⚠️ `pipeline_trener.py:112` — **мёртвый код** (строка закомментирована, функция
возвращает заглушку «OpenAI API temporarily disabled»); реального вызова whisper-1
там нет, STT тренера идёт только через ElevenLabs.

**Realtime/Azure** (отдельный конвейер): `voice_assistant/config.py:154,171`
(`gpt-4o-realtime-preview`, `gpt-4o-transcribe`), `voice_assistant/config.py:20`
(`GPT_MODEL=gpt-4o-mini` для цепочки STT→GPT→TTS).

### 15.3. Переключатели провайдеров (уже есть в коде)

| Переменная | Файл | Значения |
|---|---|---|
| `STT_PROVIDER` | `voice_assistant/config.py:50` | whisper_openai · whisper_local · elevenlabs |
| `TTS_PROVIDER` | `voice_assistant/config.py:58` | openai · elevenlabs |
| `USE_AZURE_VOICE_LIVE` | `voice_assistant/config.py:177` | true · false (⚠️ false в проде **закрывает сокет** — см. раздел 9; цепочка STT→GPT→TTS есть только в legacy `router.py`) |
| `WHISPER_MODEL/DEVICE/COMPUTE_TYPE` | `voice_assistant/config.py:42-44` | tiny..large / cpu·cuda / int8.. |
| `OPENAI_VALIDATOR_MODEL` | (env) | модель валидатора уже параметризована |

### 15.4. PII-редактор (data-flow)

`app/services/pii_redactor.py` — `redact_pii()` / `redact_pii_in_dialogue()`.
Вызовы перед LLM: `pipeline.py:930,1028,1212`, `pipeline_trener.py:279,280`.
Плейсхолдеры: `[PHONE] [EMAIL] [URL] [INN] [KPP] [OGRN] [PASSPORT] [CARD] [SNILS]
[PERSON] [COMPANY] [ADDRESS]`. Применяется к **тексту**, не к аудио.

---

## 16. Сводка: как развернуться в РФ (одна страница)

Компактная выжимка всего плана + инфраструктурного отчёта
(`UpStat_RF_Deployment_Report.pdf`). Это порядок действий целиком, от нуля до запуска.

### Шаг 0 — Организационное (параллельно с разработкой)

1. **Юрлицо РФ** (ООО/ИП) + расчётный счёт — нужно для серверов, платежей, РКН.
2. **Домен .ru** (reg.ru / nic.ru) — отдельный от КЗ-домена.
3. **Уведомление оператора ПД** в Роскомнадзор (pd.rkn.gov.ru).
4. Российские **Политика конфиденциальности** и согласие на сайте.

### Шаг 1 — Инфраструктура (~2–3 дня)

1. **App-сервер** в РФ (Selectel / Timeweb / Yandex Cloud): 4–8 vCPU, 8–16 ГБ RAM,
   Ubuntu 22.04, Docker. GPU-сервер — **только** если выбран self-host (Путь A).
2. Развернуть стек через `docker-compose` (как на текущем сервере): FastAPI,
   PostgreSQL, Redis, arq-воркеры, nginx.
3. Отдельный **`.env` для РФ** (не переиспользовать ключи КЗ). Секреты из git
   вычищены, ключи ротированы.
4. Alembic-миграции; БД **пустая** (данные граждан РФ собираются в РФ с нуля).
5. SSL (Let's Encrypt), firewall, Postgres только на localhost, бэкапы в РФ.

### Шаг 2 — AI-слой: итерация 1 «Мозг» (~1 нед.)

`ai_provider.py` → параметризация клиентов/моделей → LLM = YandexGPT
(cloud.yandex.ru, API-ключ + folder_id) или vLLM на GPU → PoC промптов.
**Результат:** анализ текста работает на РФ-LLM.

### Шаг 3 — AI-слой: итерация 2 «Уши и голос» (~1–1.5 нед.)

STT с диаризацией (SpeechKit или whisper+WhisperX) → TTS (SpeechKit/Salute) →
все клиенты `voice_assistant` на фабрику.
**Результат:** полный анализ звонка без OpenAI/ElevenLabs/Azure —
**основной продукт готов к работе в РФ**, можно запускать ограниченный релиз.

### Шаг 4 — AI-слой: итерация 3 «Realtime» (~1–2 нед.)

Драйвер STT→LLM→TTS в `websocket_handler.py`, тюнинг задержки, e2e и нагрузочные
тесты. **Результат:** голосовые тренировки без Azure.

### Шаг 5 — Обвязка и запуск

1. Вход: e-mail+пароль (уже есть) + **Yandex ID / VK ID**; Google — опционально.
2. Почта: российский SMTP (Yandex 360 / Mail.ru), SPF/DKIM.
3. Мониторинг: self-host **GlitchTip** (Sentry-совместимый, меняется только DSN).
4. Платежи (при монетизации): **YooKassa** + онлайн-касса (54-ФЗ), оферта.
5. Полный e2e-прогон → запуск.

### Критический путь

```
Юрлицо ─► серверы ─► docker-стек ─► Итерация 1 ─► Итерация 2 ─► [релиз анализа]
                                                        └─► Итерация 3 ─► [полный релиз]
```

Реалистичный срок до релиза анализа звонков: **~3–4 недели** с момента получения
серверов; полный релиз с realtime-тренировками: **~5–6 недель**.

---

> Документ описывает технический план миграции AI-слоя. Перед стартом — подтвердить
> выбор пути (A/B/C) и доступность vision/диаризации у выбранного провайдера (PoC).
