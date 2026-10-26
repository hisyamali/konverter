"""Konfigurasi logging Laravel-style dengan rotasi harian, timezone Asia/Jakarta."""

import json
import logging
import logging.config
import logging.handlers
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

JAKARTA_TZ = ZoneInfo("Asia/Jakarta")


class LaravelFormatter(logging.Formatter):
    """Formatter mirip Laravel: [Y-m-d H:i:s] channel.LEVEL: message {json_context}"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_time_format = "%Y-%m-%d %H:%M:%S"
        self.default_msec_format = "%s,%03d"

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, JAKARTA_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime(self.default_time_format)

    def format(self, record):
        ts = self.formatTime(record, self.datefmt)
        channel = getattr(record, "channel", "app")
        level = record.levelname
        msg = record.getMessage()

        ctx = getattr(record, "context", None)
        if ctx is not None:
            try:
                ctx_str = " " + json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                ctx_str = " " + str(ctx)
        else:
            ctx_str = ""

        line = f"[{ts}] {channel}.{level}: {msg}{ctx_str}"

        if record.exc_info:
            trace = self.formatException(record.exc_info).rstrip()
            line = line + " | " + trace.replace("\n", "\\n")
        elif record.stack_info:
            stack = self.formatStack(record.stack_info).rstrip()
            line = line + " | " + stack.replace("\n", "\\n")

        return line


def configure_logging(app):
    """Konfigurasi logging untuk aplikasi Flask."""
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    max_files = int(os.environ.get("LOG_MAX_FILES", "30"))
    log_channel = os.environ.get("LOG_CHANNEL", "stack").lower()

    root_path = Path(app.root_path).parent
    log_dir = root_path / "storage" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    # Handler configuration
    handlers_config = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "laravel",
            "level": log_level,
        },
        "daily": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": str(log_file),
            "when": "midnight",
            "interval": 1,
            "backupCount": max_files,
            "encoding": "utf-8",
            "formatter": "laravel",
            "level": log_level,
        },
    }

    # Pilih handler berdasarkan LOG_CHANNEL
    if log_channel == "daily":
        active_handlers = ["daily"]
    elif log_channel == "single":
        active_handlers = ["daily"]
        handlers_config["daily"]["when"] = "D"
        handlers_config["daily"]["interval"] = 1
    elif log_channel == "console":
        active_handlers = ["console"]
    else:  # stack (default)
        active_handlers = ["console", "daily"]

    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "laravel": {
                "()": LaravelFormatter,
            },
        },
        "handlers": handlers_config,
        "loggers": {
            "app": {
                "handlers": active_handlers,
                "level": log_level,
                "propagate": False,
            },
            "werkzeug": {
                "handlers": ["console"] if "console" in active_handlers else [],
                "level": "WARNING",
                "propagate": False,
            },
        },
        "root": {
            "handlers": active_handlers,
            "level": log_level,
        },
    })

    # Set app.logger ke logger "app"
    app.logger = logging.getLogger("app")
    app.logger.info("Logging configured", extra={
        "context": {"level": log_level, "channel": log_channel, "max_files": max_files}
    })

    return app.logger


def get_logger(name="app"):
    """Ambil logger instance."""
    return logging.getLogger(name)