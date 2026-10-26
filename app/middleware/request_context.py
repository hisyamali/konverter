"""Middleware untuk request context: request_id, timing, RAM monitoring, logging."""

import logging
import os
import time
import uuid
from functools import wraps

try:
    import psutil
except ImportError:
    psutil = None

from flask import g, request, current_app


def get_memory_usage_mb():
    """Ambil penggunaan memori proses saat ini dalam MB."""
    if psutil is None:
        return None
    try:
        process = psutil.Process(os.getpid())
        mem = process.memory_info()
        return round(mem.rss / 1024 / 1024, 1)
    except Exception:
        return None


def get_system_memory_percent():
    """Ambil persentase memori sistem yang terpakai."""
    if psutil is None:
        return None
    try:
        return psutil.virtual_memory().percent
    except Exception:
        return None


def register_request_logging(app):
    """Register before/after request hooks untuk logging dan monitoring."""

    @app.before_request
    def _inject_request_context():
        # Generate request_id
        g.request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        g.start_time = time.perf_counter()
        g.start_memory_mb = get_memory_usage_mb()

        # Log request masuk
        current_app.logger.info("Request started", extra={
            "context": {
                "request_id": g.request_id,
                "method": request.method,
                "path": request.path,
                "remote_addr": request.remote_addr,
                "user_agent": request.headers.get("User-Agent", "")[:100],
                "memory_mb": g.start_memory_mb,
            }
        })

    @app.after_request
    def _log_response(response):
        duration_ms = int((time.perf_counter() - g.start_time) * 1000)
        end_memory_mb = get_memory_usage_mb()
        memory_delta_mb = None
        if g.start_memory_mb is not None and end_memory_mb is not None:
            memory_delta_mb = round(end_memory_mb - g.start_memory_mb, 1)

        sys_mem_pct = get_system_memory_percent()

        current_app.logger.info("Request finished", extra={
            "context": {
                "request_id": g.request_id,
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "start_memory_mb": g.start_memory_mb,
                "end_memory_mb": end_memory_mb,
                "memory_delta_mb": memory_delta_mb,
                "system_memory_percent": sys_mem_pct,
            }
        })

        # Tambahkan header response
        response.headers["X-Request-ID"] = g.request_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        if end_memory_mb is not None:
            response.headers["X-Memory-Mb"] = str(end_memory_mb)

        return response

    @app.teardown_request
    def _teardown_request(exception=None):
        if exception:
            current_app.logger.exception("Request exception", extra={
                "context": {
                    "request_id": getattr(g, "request_id", "unknown"),
                    "method": request.method,
                    "path": request.path,
                    "error": str(exception),
                    "error_type": type(exception).__name__,
                }
            })


def log_with_context(logger, level, message, **context):
    """Helper log dengan context otomatis request_id jika tersedia."""
    ctx = dict(context)
    if hasattr(g, "request_id"):
        ctx.setdefault("request_id", g.request_id)
    logger.log(level, message, extra={"context": ctx})


def log_milestone(logger, milestone, **context):
    """Log milestone konversi dengan context standar."""
    log_with_context(logger, logging.INFO, f"Conversion {milestone}", **context)