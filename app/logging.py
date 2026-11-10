import json
import importlib
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
    ZoneInfo = importlib.import_module("backports.zoneinfo").ZoneInfo

JAKARTA_TZ = ZoneInfo("Asia/Jakarta")


class LaravelFormatter(logging.Formatter):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_time_format = "%Y-%m-%d %H:%M:%S"
        self.default_msec_format = "%s,%03d"

    def formatTime(self, record, datefmt=None):
        datetime_value = datetime.fromtimestamp(record.created, JAKARTA_TZ)
        if datefmt:
            return datetime_value.strftime(datefmt)
        return datetime_value.strftime(self.default_time_format)

    def format(self, record):
        timestamp = self.formatTime(record, self.datefmt)
        channel = getattr(record, "channel", "app")
        level = record.levelname
        message = record.getMessage()

        context = getattr(record, "context", None)
        if context is not None:
            try:
                context_text = " " + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                context_text = " " + str(context)
        else:
            context_text = ""

        line = f"[{timestamp}] {channel}.{level}: {message}{context_text}"

        if record.exc_info:
            traceback_text = self.formatException(record.exc_info).rstrip()
            line = line + " | " + traceback_text.replace("\n", "\\n")
        elif record.stack_info:
            stack_text = self.formatStack(record.stack_info).rstrip()
            line = line + " | " + stack_text.replace("\n", "\\n")

        return line


def configure_logging(app):
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    max_files = int(os.environ.get("LOG_MAX_FILES", "30"))
    log_channel = os.environ.get("LOG_CHANNEL", "stack").lower()

    root_path = Path(app.root_path).parent
    log_dir = root_path / "storage" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

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

    if log_channel == "daily":
        active_handlers = ["daily"]
    elif log_channel == "single":
        active_handlers = ["daily"]
        handlers_config["daily"]["when"] = "D"
        handlers_config["daily"]["interval"] = 1
    elif log_channel == "console":
        active_handlers = ["console"]
    else:
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

    app.logger = logging.getLogger("app")
    app.logger.info("Logging configured", extra={
        "context": {"level": log_level, "channel": log_channel, "max_files": max_files}
    })

    return app.logger


def get_logger(name="app"):
    return logging.getLogger(name)
