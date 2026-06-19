"""
Централизованная настройка структурированного логирования и Sentry.
Вызывается один раз при старте приложения через setup_logging().
"""
import logging
import os


def setup_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    try:
        from pythonjsonlogger import jsonlogger

        class _Fmt(jsonlogger.JsonFormatter):
            def add_fields(self, log_record, record, message_dict):
                super().add_fields(log_record, record, message_dict)
                log_record["service"] = "upstat"
                log_record["logger"] = record.name
                log_record["level"] = record.levelname

        handler = logging.StreamHandler()
        handler.setFormatter(_Fmt("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.root.handlers = [handler]
    except ImportError:
        logging.basicConfig(
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )

    logging.root.setLevel(log_level)

    # Sentry — только если DSN задан
    sentry_dsn = os.getenv("SENTRY_DSN", "").strip()
    if sentry_dsn:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
            from sentry_sdk.integrations.logging import LoggingIntegration

            sentry_sdk.init(
                dsn=sentry_dsn,
                integrations=[
                    FastApiIntegration(),
                    SqlalchemyIntegration(),
                    LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
                ],
                traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05")),
                environment=os.getenv("ENVIRONMENT", "development"),
                send_default_pii=False,
            )
            logging.getLogger(__name__).info("Sentry initialized", extra={"dsn_prefix": sentry_dsn[:20]})
        except ImportError:
            logging.getLogger(__name__).warning("sentry-sdk not installed — Sentry disabled")
