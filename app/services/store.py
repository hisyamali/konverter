"""Pemilih backend akun: **Supabase** atau **Appwrite**.

UI (``streamlit_app.py``) memanggil modul ini, bukan langsung ke salah satu
backend. Backend aktif ditentukan oleh:

* ``KONVERTER_BACKEND`` (env), atau
* ``[app] backend = "..."`` di ``.streamlit/secrets.toml``.

Nilai yang dikenal: ``supabase``, ``appwrite``, atau ``auto`` (default).
``auto`` memilih Supabase bila terkonfigurasi, jika tidak Appwrite. Bila tidak
ada yang siap, aplikasi berjalan sebagai **mode tamu**: semua fungsi akun &
penyimpanan menjadi no-op aman.

Sengaja tidak ada fallback otomatis saat backend error: akun tidak berpindah
sendiri antar penyedia, supaya pengguna tidak "kehilangan" akun tanpa sadar.
"""

import os
import re
from typing import Optional

try:
    import streamlit as st
except ImportError:  # pragma: no cover - hanya varian Streamlit
    st = None

from app.services import appwrite_store as _appwrite
from app.services import supabase_store as _supabase

SOURCE_BUCKET = "sources"
RESULT_BUCKET = "results"

_BACKENDS = {"supabase": _supabase, "appwrite": _appwrite}
_LABELS = {"supabase": "Supabase", "appwrite": "Appwrite"}


# ---------------------------------------------------------------------------
# Pemilihan backend
# ---------------------------------------------------------------------------
def _preference() -> str:
    """Baca preferensi backend dari env, lalu secrets ``[app] backend``."""
    preference = (os.environ.get("KONVERTER_BACKEND") or "").strip().lower()

    if preference or st is None:
        return preference

    try:
        section = st.secrets.get("app")
    except Exception:
        return ""

    if not section:
        return ""

    return (str(dict(section).get("backend") or "")).strip().lower()


def provider() -> Optional[str]:
    """Nama backend aktif (``"supabase"`` / ``"appwrite"``) atau None bila mode tamu."""
    preference = _preference()

    if preference in _BACKENDS:
        return preference if _BACKENDS[preference].is_configured() else None

    for name in ("supabase", "appwrite"):
        if _BACKENDS[name].is_configured():
            return name

    return None


def provider_label() -> str:
    """Nama backend yang ramah ditampilkan (kosong bila mode tamu)."""
    return _LABELS.get(provider() or "", "")


def backend():
    """Modul backend aktif, atau None bila mode tamu."""
    name = provider()
    return _BACKENDS[name] if name else None


def _require():
    module = backend()

    if module is None:
        raise RuntimeError("Backend akun belum dikonfigurasi (Supabase/Appwrite).")

    return module


def is_configured() -> bool:
    """True bila ada backend akun yang siap dipakai."""
    return provider() is not None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def current_user() -> Optional[dict]:
    """Ringkasan pengguna yang login, atau None untuk mode tamu."""
    module = backend()
    return module.current_user() if module else None


def sign_up(email: str, password: str) -> dict:
    """Daftarkan akun baru di backend aktif."""
    return _require().sign_up(email, password)


def sign_in(email: str, password: str) -> dict:
    """Masuk ke backend aktif."""
    return _require().sign_in(email, password)


def sign_out() -> None:
    """Keluar dari backend aktif (no-op di mode tamu)."""
    module = backend()

    if module:
        module.sign_out()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def object_path(user_id: str, job_id: str, filename: str) -> str:
    """Konvensi referensi objek: ``<user_id>/<job_id>/<nama-file>``."""
    module = backend()

    if module:
        return module.object_path(user_id, job_id, filename)

    base = os.path.basename(filename or "file")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "file"
    return f"{user_id}/{job_id}/{cleaned[:120]}"


def upload_file(bucket: str, path: str, data: bytes, content_type: str) -> None:
    """Unggah file ke bucket backend aktif."""
    _require().upload_file(bucket, path, data, content_type)


def signed_url(bucket: str, path: str, expires: Optional[int] = None) -> Optional[str]:
    """URL unduhan sementara (Supabase); None bila backend tidak mendukungnya."""
    module = backend()

    if module is None:
        return None

    if expires is None:
        return module.signed_url(bucket, path)

    return module.signed_url(bucket, path, expires)


def download_bytes(bucket: str, path: str) -> Optional[bytes]:
    """Isi file dari storage (dipakai backend tanpa signed URL)."""
    module = backend()

    if module is None:
        return None

    fetcher = getattr(module, "download_bytes", None)
    return fetcher(bucket, path) if fetcher else None


def remove_objects(bucket: str, paths) -> None:
    """Hapus objek dari bucket backend aktif."""
    module = backend()

    if module:
        module.remove_objects(bucket, paths)


# ---------------------------------------------------------------------------
# Riwayat pekerjaan
# ---------------------------------------------------------------------------
def create_job(user_id: str, conversion_type: str, original_name: str, source_size: int,
               source_path: Optional[str]) -> Optional[str]:
    """Sisipkan baris pekerjaan baru; kembalikan id atau None."""
    module = backend()
    return module.create_job(user_id, conversion_type, original_name, source_size, source_path) if module else None


def finish_job(job_id: Optional[str], status: str, **fields) -> None:
    """Perbarui status pekerjaan (no-op di mode tamu)."""
    module = backend()

    if module:
        module.finish_job(job_id, status, **fields)


def list_jobs(user_id: str, limit: int = 50) -> list:
    """Riwayat pekerjaan milik pengguna (kosong di mode tamu)."""
    module = backend()
    return module.list_jobs(user_id, limit) if module else []


def delete_job(user_id: str, job: dict) -> None:
    """Hapus berkas + baris pekerjaan (no-op di mode tamu)."""
    module = backend()

    if module:
        module.delete_job(user_id, job)


# ---------------------------------------------------------------------------
# Diagnostik
# ---------------------------------------------------------------------------
def _http_health(url: str, headers: Optional[dict] = None) -> tuple:
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx ikut terpasang bersama supabase
        return False, "httpx tidak tersedia"

    try:
        response = httpx.get(url, headers=headers or {}, timeout=6.0)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    return response.status_code < 400, f"HTTP {response.status_code}"


def health() -> dict:
    """Tes koneksi ringan ke backend akun aktif (dipakai tombol \"Tes koneksi\")."""
    name = provider()

    if name is None:
        return {"provider": None, "ok": False, "host": "", "detail": "Belum ada backend akun yang dikonfigurasi."}

    module = _BACKENDS[name]
    config = module._config()

    if name == "appwrite":
        host = config["endpoint"]
        ok, detail = _http_health(host.rstrip("/") + "/health")
    else:
        host = config["url"]
        ok, detail = _http_health(host.rstrip("/") + "/auth/v1/health", {"apikey": config["anon_key"]})

    return {"provider": name, "ok": ok, "host": host, "detail": detail}
