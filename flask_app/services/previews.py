import threading
import time
from pathlib import Path

from flask import current_app

try:
    import psutil
except ImportError:
    psutil = None

PREVIEW_MAX_AGE_SECONDS = 86400

_previews_lock = threading.Lock()
_preview_registry = {}


def _get_memory_usage_mb():
    if psutil is None:
        return None
    try:
        process = psutil.Process()
        return round(process.memory_info().rss / 1024 / 1024, 1)
    except Exception:
        return None


def _log_preview(action, preview_id, **context):
    if current_app:
        current_app.logger.info(f"Preview {action}", extra={"context": {"preview_id": preview_id, **context}})


def register_preview(preview_id, path, original_name, conversion_type, meta=None):
    with _previews_lock:
        _purge_expired()
        _preview_registry[preview_id] = {
            "path": Path(path),
            "original_name": original_name,
            "conversion_type": conversion_type,
            "created_at": time.time(),
            "meta": dict(meta) if meta else {},
        }

    _log_preview("register", preview_id, original_name=original_name, conversion_type=conversion_type, memory_mb=_get_memory_usage_mb())

    return preview_id


def get_preview(preview_id):
    if not preview_id:
        return None

    with _previews_lock:
        _purge_expired()
        preview_record = _preview_registry.get(preview_id)

        if preview_record is None:
            return None

        if not preview_record["path"].exists():
            del _preview_registry[preview_id]
            _log_preview("get_missing_file", preview_id)
            return None

    _log_preview("get", preview_id, original_name=preview_record["original_name"], conversion_type=preview_record["conversion_type"])
    return dict(preview_record)


def delete_preview(preview_id):
    with _previews_lock:
        preview_record = _preview_registry.pop(preview_id, None)

    if preview_record is not None:
        try:
            preview_record["path"].unlink(missing_ok=True)
        except OSError:
            pass
        _log_preview("delete", preview_id, original_name=preview_record["original_name"])


def sweep_old_files(folder, max_age_seconds=PREVIEW_MAX_AGE_SECONDS):
    try:
        folder_entries = list(folder.iterdir())
    except OSError:
        return 0

    current_time = time.time()
    deleted_count = 0

    for path in folder_entries:
        try:
            if path.name == ".gitkeep":
                continue

            if not path.is_file():
                continue

            if current_time - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)
                deleted_count += 1
        except OSError:
            continue

    if deleted_count:
        if current_app:
            current_app.logger.info("Sweep completed", extra={"context": {"folder": str(folder), "deleted": deleted_count}})

    return deleted_count


def _purge_expired():
    current_time = time.time()
    expired_preview_ids = [
        preview_id
        for preview_id, preview_record in _preview_registry.items()
        if current_time - preview_record.get("created_at", current_time) > PREVIEW_MAX_AGE_SECONDS
    ]

    for preview_id in expired_preview_ids:
        preview_record = _preview_registry.pop(preview_id, None)

        if preview_record is not None:
            try:
                preview_record["path"].unlink(missing_ok=True)
            except OSError:
                pass
            _log_preview("purge_expired", preview_id, original_name=preview_record.get("original_name"))
