"""
Маскирование ПД в транскрипциях — обёртка-проксирование
из основного модуля app/services/pii_redactor.py.

Если основной модуль недоступен по sys.path (например, при запуске
ai_agent_service как отдельного процесса), используется встроенная копия.
"""

import sys
import os

# Попробуем импортировать из основного app-пакета
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_app_dir = os.path.join(_project_root, "app")
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

try:
    from services.pii_redactor import redact_pii, redact_pii_in_dialogue  # noqa: F401
except ImportError:
    try:
        from app.services.pii_redactor import redact_pii, redact_pii_in_dialogue  # noqa: F401
    except ImportError:
        # Fallback: no-op если модуль не найден (не ломаем сервис)
        import logging
        logging.getLogger(__name__).warning(
            "pii_redactor not found — PII redaction disabled"
        )

        def redact_pii(text: str) -> str:  # type: ignore[misc]
            return text

        def redact_pii_in_dialogue(dialogue):  # type: ignore[misc]
            return dialogue
