"""
Central logging setup, applied once at process startup (see app/main.py).

Always logs to stdout, never to a file -- per 12-factor, it's the
deploying platform's job (Docker/systemd/k8s) to collect and ship logs,
not the app's. Format is switchable via LOG_FORMAT:
  - "text" (default) -- human-readable, for local dev
  - "json" -- one JSON object per line, for environments that ingest
    structured logs (CloudWatch, Loki, Datadog, etc.)
"""
import json
import logging
import logging.config
from datetime import datetime, timezone

from app.core.config import settings


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    formatter = "json" if settings.log_format == "json" else "plain"

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "plain": {
                    "format": "%(asctime)s %(levelname)-8s %(name)s %(message)s",
                },
                "json": {
                    "()": "app.core.logging.JSONFormatter",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": formatter,
                    "stream": "ext://sys.stdout",
                },
            },
            "root": {
                "handlers": ["console"],
                "level": "INFO",
            },
            "loggers": {
                # Let uvicorn's own loggers use our handler/format instead
                # of its default config, so output is consistent.
                "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
                # SQLAlchemy at INFO echoes every SQL statement -- too noisy.
                "sqlalchemy.engine": {"handlers": ["console"], "level": "WARNING", "propagate": False},
            },
        }
    )
