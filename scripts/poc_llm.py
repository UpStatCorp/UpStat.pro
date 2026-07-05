#!/usr/bin/env python3
"""
PoC-проверка LLM-провайдера (этап 2 миграции AI-слоя).

Использует ту же конфигурацию, что и продакшн (services.ai_provider), поэтому
проверяет РЕАЛЬНЫЙ путь: клиент, base_url, имя модели, авторизацию.

Запуск (из корня репозитория):
    # OpenAI (как эталон):
    PYTHONPATH=app python scripts/poc_llm.py

    # YandexGPT:
    LLM_PROVIDER=yandex YANDEX_API_KEY=AQVN... YANDEX_FOLDER_ID=b1g... \
        PYTHONPATH=app python scripts/poc_llm.py

Что делает:
  1) печатает разрешённую конфигурацию (провайдер, base_url, модель);
  2) простой smoke-запрос («скажи 'ok'»);
  3) запрос в стиле анализа звонка с требованием JSON — проверяет, что модель
     возвращает валидный JSON нужной формы (response_format + скоринг);
  4) сообщает, поддержала ли модель response_format.

Ничего не пишет в БД. Транскрипт — синтетический и обезличенный.
"""

import json
import os
import sys

# Позволяем запускать и как `python scripts/poc_llm.py`, и с PYTHONPATH=app.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

# Загружаем .env из корня репозитория (как это делает продакшн), чтобы скрипт
# видел OPENAI_API_KEY / LLM_PROVIDER / YANDEX_*.  Инлайновые переменные окружения
# имеют приоритет (override=False), поэтому можно и так:
#   LLM_PROVIDER=yandex YANDEX_API_KEY=... YANDEX_FOLDER_ID=... python scripts/poc_llm.py
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from services import ai_provider as ap  # noqa: E402

SAMPLE_TRANSCRIPT = (
    "Менеджер: Здравствуйте! Вы оставляли заявку на курс. Удобно говорить?\n"
    "Клиент: Да, но я пока сомневаюсь, дорого.\n"
    "Менеджер: Понимаю. А что именно в программе вам откликнулось?\n"
    "Клиент: Практика и разбор реальных сделок.\n"
    "Менеджер: Отлично, тогда предлагаю оформить на старт потока со скидкой.\n"
    "Клиент: Хорошо, давайте попробуем.\n"
)

ANALYSIS_PROMPT = (
    "Ты — аудитор звонков по продажам. Проанализируй обезличенный диалог и верни "
    "СТРОГО JSON без пояснений в формате: "
    '{"score": <0-100 int>, "summary": <строка>, '
    '"strengths": [<строки>], "improvements": [<строки>]}.\n\n'
    f"Диалог:\n{SAMPLE_TRANSCRIPT}"
)


def _print_header():
    print("=" * 64)
    print("PoC LLM  ·  провайдер:", ap.provider())
    print("base_url :", ap._base_url() or "https://api.openai.com/v1 (default)")
    print("model    :", ap.model_main())
    key = ap._api_key()
    print("api_key  :", (key[:6] + "…") if key and key != "EMPTY" else key)
    print("=" * 64)


def _smoke(client, model):
    print("\n[1] Smoke-запрос…")
    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Ответь одним словом: ok"}],
        temperature=0,
    )
    print("    ответ:", (r.choices[0].message.content or "").strip()[:120])


def _analysis(client, model):
    print("\n[2] Анализ звонка (JSON)…")
    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": ANALYSIS_PROMPT}],
        "temperature": 0.2,
    }
    # Пробуем со строгим JSON-режимом; если провайдер не поддерживает — повторяем без него.
    used_response_format = True
    try:
        r = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
    except Exception as e:  # noqa: BLE001
        print("    ⚠️ response_format не поддержан провайдером:", type(e).__name__, str(e)[:80])
        used_response_format = False
        r = client.chat.completions.create(**kwargs)

    raw = (r.choices[0].message.content or "").strip()
    print("    response_format:", "OK" if used_response_format else "НЕ поддержан (fallback)")
    print("    сырой ответ (обрезан):", raw[:200])
    try:
        data = json.loads(raw)
        ok = isinstance(data.get("score"), int) and "summary" in data
        print("    JSON распарсен:", "✅" if ok else "⚠️ структура не совпала", "| score =", data.get("score"))
    except Exception as e:  # noqa: BLE001
        print("    ❌ ответ не является валидным JSON:", type(e).__name__, str(e)[:80])


def main():
    _print_header()
    client = ap.get_llm_client()
    model = ap.model_main()
    try:
        _smoke(client, model)
        _analysis(client, model)
        print("\nГотово. Сравните score/summary с эталоном OpenAI и при необходимости "
              "подкрутите промпты в таблице prompts под новую модель.")
    except Exception as e:  # noqa: BLE001
        print("\n❌ Ошибка вызова:", type(e).__name__, str(e)[:200])
        print("Проверьте: LLM_PROVIDER, YANDEX_API_KEY/FOLDER_ID, роль сервисного аккаунта, доступ к сети.")
        sys.exit(1)


if __name__ == "__main__":
    main()
