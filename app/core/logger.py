"""app/core/logger.py — Structured logging with JSON (prod) and colour (dev)."""

import json
import logging
import logging.config
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="no-request")

_REDACTED = "***REDACTED***"
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "api_key",
        "stripe_customer_id",
        "stripe_subscription_id",
        "authorization",
        "jwt_secret_key",
        "anthropic_api_key",
    }
)


def get_request_id() -> str:
    return request_id_var.get()


def set_request_id(rid: str | None = None) -> str:
    rid = rid or str(uuid.uuid4())
    request_id_var.set(rid)
    return rid


def _redact(data: dict) -> dict:
    return {
        k: (
            _REDACTED
            if k.lower() in _SENSITIVE_KEYS
            else (_redact(v) if isinstance(v, dict) else v)
        )
        for k, v in data.items()
    }


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        known = {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "taskName",
        }
        extras = {k: record.__dict__[k] for k in set(record.__dict__) - known}
        if extras:
            log["extra"] = _redact(extras)
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        return json.dumps(log, default=str)


_COLOURS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[35m",
}
_RESET = "\033[0m"


class DevFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        c = _COLOURS.get(record.levelname, "")
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        rid = get_request_id()[:8]
        line = f"{c}[{record.levelname.ljust(8)}]{_RESET} {ts} | {rid} | {record.name} | {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _build_config(level: str, json_logs: bool) -> dict:
    fmt = "json" if json_logs else "dev"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {"()": JSONFormatter},
            "dev": {"()": DevFormatter},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": fmt,
            },
        },
        "loggers": {
            "app": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
            "uvicorn.access": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
            "sqlalchemy.engine": {
                "handlers": ["console"],
                "level": "WARNING",
                "propagate": False,
            },
        },
        "root": {"handlers": ["console"], "level": "WARNING"},
    }


def setup_logging() -> None:
    json_logs = settings.ENVIRONMENT == "production"
    logging.config.dictConfig(_build_config(settings.LOG_LEVEL.upper(), json_logs))
    get_logger(__name__).info(
        "Logging initialised",
        extra={"environment": settings.ENVIRONMENT, "json_mode": json_logs},
    )


def get_logger(name: str) -> logging.Logger:
    if not name.startswith("app."):
        name = f"app.{name}"
    return logging.getLogger(name)
