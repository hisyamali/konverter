import threading
import time
from pathlib import Path

PREVIEW_MAX_AGE_SECONDS = 86400

_lock = threading.Lock()
_previews = {}


def register_preview(preview_id, path, original_name, conversion_type, meta=None):
    with _lock:
        _purge_expired()
        _previews[preview_id] = {
            "path": Path(path),
            "original_name": original_name,
            "conversion_type": conversion_type,
            "created_at": time.time(),
            "meta": dict(meta) if meta else {},
        }

    return preview_id


def get_preview(preview_id):
    if not preview_id:
        return None

    with _lock:
        _purge_expired()
        preview = _previews.get(preview_id)

        if preview is None:
            return None

        if not preview["path"].exists():
            del _previews[preview_id]
            return None

        return dict(preview)


def delete_preview(preview_id):
    with _lock:
        preview = _previews.pop(preview_id, None)

    if preview is not None:
        try:
            preview["path"].unlink(missing_ok=True)
        except OSError:
            pass


def sweep_old_files(folder, max_age_seconds=PREVIEW_MAX_AGE_SECONDS):
    try:
        folder_iter = list(folder.iterdir())
    except OSError:
        return

    now = time.time()

    for path in folder_iter:
        try:
            if path.name == ".gitkeep":
                continue

            if not path.is_file():
                continue

            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _purge_expired():
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