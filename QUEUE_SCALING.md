# Очередь анализов (arq) — масштабирование и эксплуатация

Тяжёлые анализы (звонки/чаты/тренер) выполняются НЕ в веб-процессе, а в отдельном
`worker`-сервисе, который читает задачи из Redis (arq). Это даёт глобальный потолок
конкурентности, честность между юзерами, переживание рестартов и горизонтальное
масштабирование.

## Архитектура

```
web (uvicorn ×WEB_CONCURRENCY)  --enqueue-->  Redis (arq)  --pull-->  worker ×WORKER_REPLICAS
   эндпоинт сразу отвечает job_id                                       выполняет анализ
   статус → Postgres (sync_status), прогресс → Redis, уведомления → Postgres
```

Точки запуска переведены на очередь за флагом `USE_QUEUE` (CRM single/batch, chat
audio/text/raw_text/images, chat_trener ×4). Батч = N отдельных per-record job.

## Гарантии

- **Глобальный потолок** одновременных анализов = `WORKER_REPLICAS × WORKER_MAX_JOBS`.
- **Fairness**: не более `PER_USER_CONCURRENCY` анализов на юзера одновременно
  (sliding-window семафор в Redis, само-исцеляется от утечек по TTL). Сверх лимита
  джоб переставляется в очередь с задержкой `FAIRNESS_DEFER_SECONDS`
  (до `FAIRNESS_MAX_ATTEMPTS` раз, затем выполняется, чтобы не зависнуть навсегда).
- **Идемпотентность**: per-object Redis-лок — повтор/ретрай не запустит второй анализ
  той же CRM-записи или того же chat-вложения.
- **Без дублей**: пайплайны анализа НЕ идемпотентны (повторный прогон создаёт дубли
  сообщений и новую Conversation), поэтому авто-ретрай по умолчанию выключен
  (`WORKER_MAX_TRIES=1`). Per-object Redis-лок защищает от конкурентных дублей
  (двойной клик / двойной enqueue). **Восстановление после краша воркера:** CRM —
  повторный запуск записи по таймауту (`STUCK_RECORDING_TIMEOUT_MIN`), chat —
  повторная загрузка пользователем. Поднимать `WORKER_MAX_TRIES` можно ТОЛЬКО
  сделав пайплайны идемпотентными (guard «отчёт по (conversation_id, ref) уже есть»).

## Формула сайзинга

Пиковая нагрузка на OpenAI (одновременных GPT-вызовов):

```
peak_llm ≈ WORKER_REPLICAS × WORKER_MAX_JOBS × CHECKLIST_CONCURRENCY
```

Подбирайте так, чтобы `peak_llm` укладывался в RPM/TPM вашего OpenAI tier.
Пример (старт на 50–500 юзеров): `2 × 8 × 4 = 64` одновременных GPT-вызова.
Если ловите `429` — снижайте `WORKER_MAX_JOBS` или `CHECKLIST_CONCURRENCY`
(клиент OpenAI уже ретраит 429 с backoff, но лучше не упираться).

Коннекты Postgres (каждый анализ держит 1 сессию на всё время):

```
peak_pg ≈ WEB_CONCURRENCY × 60  +  WORKER_REPLICAS × WORKER_MAX_JOBS × (1..2)
POSTGRES_MAX_CONNECTIONS должен быть заметно больше peak_pg
```

Дефолт `POSTGRES_MAX_CONNECTIONS=300` покрывает web(4×60=240) + умеренный worker-пул.
При росте реплик — поднимайте.

## Запуск

```bash
cp .env.example .env            # заполнить значения, USE_QUEUE=true
docker compose run --rm backend alembic upgrade head
docker compose up -d                       # web + worker (replicas из WORKER_REPLICAS)
docker compose up -d --scale worker=4      # увеличить число воркеров под нагрузку
docker compose logs -f worker              # логи воркера
```

## Откат

`USE_QUEUE=false` → анализы снова идут через in-process BackgroundTasks (старое
поведение), worker-сервис можно остановить. Мгновенный безопасный откат.

## Ключевые env-переменные

| Переменная | Дефолт | Назначение |
|------------|--------|------------|
| `USE_QUEUE` | false | вкл. очередь (иначе BackgroundTasks) |
| `WORKER_REPLICAS` | 2 | число worker-контейнеров |
| `WORKER_MAX_JOBS` | 8 | одновременных анализов на воркер |
| `WORKER_JOB_TIMEOUT` | 1800 | таймаут анализа, сек |
| `WORKER_MAX_TRIES` | 1 | авто-ретрай выкл. (пайплайны не идемпотентны) |
| `PER_USER_CONCURRENCY` | 2 | анализов на юзера одновременно |
| `CHECKLIST_CONCURRENCY` | 4 | параллельных чек-листов в анализе |
| `POSTGRES_MAX_CONNECTIONS` | 300 | лимит коннектов Postgres |
| `PROGRESS_TTL_SECONDS` | 86400 | TTL прогресса в Redis |

> ⚠️ worker-сервис ОБЯЗАН монтировать тот же том `uploads_data:/app/uploads`, что и
> backend (воркер пишет файлы отчётов, web их отдаёт). Уже настроено в docker-compose.yml.
