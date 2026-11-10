import os
import re
from typing import Optional

try:
    import streamlit
except ImportError:
    streamlit = None

from flask_app.services import appwrite_store
from flask_app.services import supabase_store

SOURCE_BUCKET = "sources"
RESULT_BUCKET = "results"

_BACKEND_MODULES = {"supabase": supabase_store, "appwrite": appwrite_store}
_BACKEND_LABELS = {"supabase": "Supabase", "appwrite": "Appwrite"}


def _preference() -> str:
    preference = (os.environ.get("KONVERTER_BACKEND") or "").strip().lower()

    if preference or streamlit is None:
        return preference

    try:
        secrets_section = streamlit.secrets.get("app")
    except Exception:
        return ""

    if not secrets_section:
        return ""

    return (str(dict(secrets_section).get("backend") or "")).strip().lower()


def provider() -> Optional[str]:
    preference = _preference()

    if preference in _BACKEND_MODULES:
        return preference if _BACKEND_MODULES[preference].is_configured() else None

    for backend_name in ("supabase", "appwrite"):
        if _BACKEND_MODULES[backend_name].is_configured():
            return backend_name

    return None


def provider_label() -> str:
    return _BACKEND_LABELS.get(provider() or "", "")


def backend():
    backend_name = provider()
    return _BACKEND_MODULES[backend_name] if backend_name else None


def _require():
    backend_module = backend()

    if backend_module is None:
        raise RuntimeError("Backend akun belum dikonfigurasi (Supabase/Appwrite).")

    return backend_module


def is_configured() -> bool:
    return provider() is not None


def current_user() -> Optional[dict]:
    backend_module = backend()
    return backend_module.current_user() if backend_module else None


def sign_up(email: str, password: str) -> dict:
    return _require().sign_up(email, password)


def sign_in(email: str, password: str) -> dict:
    return _require().sign_in(email, password)


def sign_out() -> None:
    backend_module = backend()

    if backend_module:
        backend_module.sign_out()


def object_path(user_id: str, job_id: str, filename: str) -> str:
    backend_module = backend()

    if backend_module:
        return backend_module.object_path(user_id, job_id, filename)

    base_name = os.path.basename(filename or "file")
    cleaned_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name).strip("._") or "file"
    return f"{user_id}/{job_id}/{cleaned_name[:120]}"


def upload_file(bucket: str, path: str, data: bytes, content_type: str) -> None:
    _require().upload_file(bucket, path, data, content_type)


def signed_url(bucket: str, path: str, expires: Optional[int] = None) -> Optional[str]:
    backend_module = backend()

    if backend_module is None:
        return None

    if expires is None:
        return backend_module.signed_url(bucket, path)

    return backend_module.signed_url(bucket, path, expires)


def download_bytes(bucket: str, path: str) -> Optional[bytes]:
    backend_module = backend()

    if backend_module is None:
        return None

    download_function = getattr(backend_module, "download_bytes", None)
    return download_function(bucket, path) if download_function else None


def remove_objects(bucket: str, paths) -> None:
    backend_module = backend()

    if backend_module:
        backend_module.remove_objects(bucket, paths)


def create_job(user_id: str, conversion_type: str, original_name: str, source_size: int,
               source_path: Optional[str]) -> Optional[str]:
    backend_module = backend()
    return backend_module.create_job(user_id, conversion_type, original_name, source_size, source_path) if backend_module else None


def finish_job(job_id: Optional[str], status: str, **fields) -> None:
    backend_module = backend()

    if backend_module:
        backend_module.finish_job(job_id, status, **fields)


def list_jobs(user_id: str, limit: int = 50) -> list:
    backend_module = backend()
    return backend_module.list_jobs(user_id, limit) if backend_module else []


def delete_job(user_id: str, job: dict) -> None:
    backend_module = backend()

    if backend_module:
        backend_module.delete_job(user_id, job)


def _http_health(url: str, headers: Optional[dict] = None) -> tuple:
    try:
        import httpx
    except ImportError:
        return False, "httpx tidak tersedia"

    try:
        response = httpx.get(url, headers=headers or {}, timeout=6.0)
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"

    return response.status_code < 400, f"HTTP {response.status_code}"


def health() -> dict:
    backend_name = provider()

    if backend_name is None:
        return {"provider": None, "ok": False, "host": "", "detail": "Belum ada backend akun yang dikonfigurasi."}

    backend_module = _BACKEND_MODULES[backend_name]
    config = backend_module._config()

    if backend_name == "appwrite":
        host = config["endpoint"]
        is_healthy, detail = _http_health(host.rstrip("/") + "/health")
    else:
        host = config["url"]
        is_healthy, detail = _http_health(host.rstrip("/") + "/auth/v1/health", {"apikey": config["anon_key"]})

    return {"provider": backend_name, "ok": is_healthy, "host": host, "detail": detail}
