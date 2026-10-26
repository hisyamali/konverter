"""Registry pratinjau upload sementara sebelum file benar-benar dikonversi."""

import threading
import time
from pathlib import Path

from flask import current_app

try:
    import psutil
except ImportError:
    psutil = None

PREVIEW_MAX_AGE_SECONDS = 86400

_lock = threading.Lock()
_previews = {}


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
    """Simpan metadata file yang sudah lolos pratinjau untuk dipakai saat konversi."""
    with _lock:
        _purge_expired()
        _previews[preview_id] = {
            "path": Path(path),
            "original_name": original_name,
            "conversion_type": conversion_type,
            "created_at": time.time(),
            "meta": dict(meta) if meta else {},
        }

    _log_preview("register", preview_id, original_name=original_name, conversion_type=conversion_type, memory_mb=_get_memory_usage_mb())

    return preview_id


def get_preview(preview_id):
    """Ambil metadata pratinjau jika ID masih valid dan file fisiknya masih ada."""
    if not preview_id:
        return None

    with _lock:
        _purge_expired()
        preview = _previews.get(preview_id)

        if preview is None:
            return None

        if not preview["path"].exists():
            del _previews[preview_id]
            _log_preview("get_missing_file", preview_id)
            return None

    _log_preview("get", preview_id, original_name=preview["original_name"], conversion_type=preview["conversion_type"])
    return dict(preview)


def delete_preview(preview_id):
    """Hapus metadata pratinjau sekaligus file upload sementaranya."""
    with _lock:
        preview = _previews.pop(preview_id, None)

    if preview is not None:
        try:
            preview["path"].unlink(missing_ok=True)
        except OSError:
            pass
        _log_preview("delete", preview_id, original_name=preview["original_name"])


def sweep_old_files(folder, max_age_seconds=PREVIEW_MAX_AGE_SECONDS):
    """Bersihkan file lama di folder storage tanpa menghapus file penjaga .gitkeep."""
    try:
        folder_iter = list(folder.iterdir())
    except OSError:
        return 0

    now = time.time()
    deleted = 0

    for path in folder_iter:
        try:
            if path.name == ".gitkeep":
                continue

            if not path.is_file():
                continue

            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)
                deleted += 1
        except OSError:
            continue

    if deleted:
        if current_app:
            current_app.logger.info("Sweep completed", extra={"context": {"folder": str(folder), "deleted": deleted}})

    return deleted


def _purge_expired():
    """Hapus entri pratinjau yang melewati masa berlaku 24 jam."""
    now = time.time()
    expired = [
        key
        for key, preview in _previews.items()
        if now - preview.get("created_at", now) > PREVIEW_MAX_AGE_SECONDS
    ]

    for key in expired:
        preview = _previews.pop(key, None)

        if preview is not None:
            try:
                preview["path"].unlink(missing_ok=True)
            except OSError:
                pass
            _log_preview("purge_expired", key, original_name=preview.get("original_name"))
