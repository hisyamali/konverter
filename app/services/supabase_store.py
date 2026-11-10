"""Integrasi Supabase untuk varian Streamlit: Auth, tabel ``jobs``, dan Storage.

Alur iLovePDF-style memakai modul ini untuk tiga hal:

1. **Auth** opsional (email + password). Tanpa login aplikasi tetap bisa
   mengonversi (mode tamu, file hanya diproses sementara lalu dihapus).
2. **Riwayat**: metadata pekerjaan disimpan di tabel ``public.jobs``.
3. **Penyimpanan privat**: file sumber dan hasil ``.xlsx`` di bucket privat
   ``sources`` / ``results``; akses hanya lewat *signed URL* milik pemiliknya.

Semua operasi defensif: bila Supabase belum dikonfigurasi atau terjadi
kesalahan jaringan, aplikasi tetap jalan sebagai mode tamu.
"""

import os
import re
from typing import Optional

try:
    import streamlit as st
except ImportError:
    st = None

try:
    from supabase import Client, create_client
except ImportError:
    Client = None
    create_client = None

SOURCE_BUCKET = "sources"
RESULT_BUCKET = "results"
SIGNED_URL_TTL = 3600

_CLIENT_KEY = "_sb_client"
_SESSION_KEY = "_sb_session"
_USER_KEY = "_sb_user"


def _secrets_section() -> dict:
    """Baca blok ``[supabase]`` dari ``st.secrets`` tanpa gagal bila belum ada."""
    if st is None:
        return {}

    try:
        section = st.secrets.get("supabase")
    except Exception:
        return {}

    return dict(section) if section else {}


def _pick(secret_value, env_name: str) -> str:
    """Ambil dari secrets; bila kosong/placeholder, jatuh ke variabel lingkungan."""
    value = secret_value.strip() if isinstance(secret_value, str) else ""

    if value and "<" not in value:
        return value

    return (os.environ.get(env_name) or "").strip()


def _config() -> dict:
    """Gabungkan konfigurasi dari ``st.secrets`` (diutamakan) dan env var."""
    secrets = _secrets_section()
    return {
        "url": _pick(secrets.get("url"), "SUPABASE_URL"),
        "anon_key": _pick(secrets.get("anon_key"), "SUPABASE_ANON_KEY"),
    }


def is_configured() -> bool:
    """True bila URL dan anon key Supabase sudah diisi (bukan placeholder)."""
    if create_client is None:
        return False

    config = _config()
    if not config["url"] or not config["anon_key"]:
        return False

    return "<" not in config["url"] and "<" not in config["anon_key"]


def get_client() -> Optional["Client"]:
    """Klien Supabase **per sesi** (tidak dibagikan antar pengguna)."""
    if not is_configured() or st is None:
        return None

    client = st.session_state.get(_CLIENT_KEY)

    if client is None:
        config = _config()
        try:
            client = create_client(config["url"], config["anon_key"])
        except Exception:
            return None

        st.session_state[_CLIENT_KEY] = client
        _restore_session(client)

    return client


def _restore_session(client) -> None:
    """Terapkan kembali token sesi ke klien baru (bertahan antar rerun)."""
    session = st.session_state.get(_SESSION_KEY)

    if not session:
        return

    try:
        client.auth.set_session(session["access_token"], session["refresh_token"])
    except Exception:
        st.session_state.pop(_SESSION_KEY, None)
        st.session_state.pop(_USER_KEY, None)


def _remember_user(user) -> dict:
    """Simpan ringkasan pengguna + token sesi di session_state."""
    info = {"id": user.id, "email": getattr(user, "email", "") or ""}
    st.session_state[_USER_KEY] = info

    try:
        session = client_session()
        if session:
            st.session_state[_SESSION_KEY] = {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
            }
    except Exception:
        pass

    return info


_client_for_session = None


def client_session():
    """Ambil objek sesi aktif dari klien di session_state (tanpa jaringan)."""
    client = st.session_state.get(_CLIENT_KEY)

    if client is None:
        return None

    try:
        return client.auth.get_session()
    except Exception:
        return None


def current_user() -> Optional[dict]:
    """Ringkasan pengguna yang login, atau None untuk mode tamu."""
    if st is None:
        return None

    return st.session_state.get(_USER_KEY)


def sign_up(email: str, password: str) -> dict:
    """Daftarkan akun baru. Bila konfirmasi email aktif, sesi belum terbuat."""
    client = get_client()

    if client is None:
        raise RuntimeError("Supabase belum dikonfigurasi.")

    response = client.auth.sign_up({"email": email, "password": password})

    if getattr(response, "session", None):
        _remember_user(response.user)
        return {"needs_confirmation": False, "user": current_user()}

    return {"needs_confirmation": True, "user": None}


def sign_in(email: str, password: str) -> dict:
    """Masuk dengan email + password."""
    client = get_client()

    if client is None:
        raise RuntimeError("Supabase belum dikonfigurasi.")

    response = client.auth.sign_in_with_password({"email": email, "password": password})
    return _remember_user(response.user)


def sign_out() -> None:
    """Keluar dan buang klien + token dari sesi."""
    if st is None:
        return

    client = st.session_state.get(_CLIENT_KEY)

    if client is not None:
        try:
            client.auth.sign_out()
        except Exception:
            pass

    for key in (_CLIENT_KEY, _SESSION_KEY, _USER_KEY):
        st.session_state.pop(key, None)


def _safe_name(name: str) -> str:
    """Bersihkan nama file agar aman dipakai sebagai objek storage."""
    base = os.path.basename(name or "file")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "file"
    return cleaned[:120]


def object_path(user_id: str, job_id: str, filename: str) -> str:
    """Konvensi path: ``<user_id>/<job_id>/<filename>`` (dipakai kebijakan RLS)."""
    return f"{user_id}/{job_id}/{_safe_name(filename)}"


def upload_file(bucket: str, path: str, data: bytes, content_type: str) -> None:
    """Unggah byte ke bucket (upsert agar aman diulang)."""
    client = get_client()

    if client is None:
        raise RuntimeError("Supabase belum dikonfigurasi.")

    client.storage.from_(bucket).upload(
        path,
        data,
        {"content-type": content_type or "application/octet-stream", "upsert": "true"},
    )


def signed_url(bucket: str, path: str, expires: int = SIGNED_URL_TTL) -> Optional[str]:
    """Buat signed URL sementara untuk objek privat."""
    client = get_client()

    if client is None:
        return None

    try:
        result = client.storage.from_(bucket).create_signed_url(path, expires)
    except Exception:
        return None

    if isinstance(result, dict):
        return result.get("signedURL") or result.get("signedUrl")

    return getattr(result, "signed_url", None)


def remove_objects(bucket: str, paths) -> None:
    """Hapus objek dari bucket (abaikan bila gagal)."""
    client = get_client()

    if client is None or not paths:
        return

    try:
        client.storage.from_(bucket).remove(list(paths))
    except Exception:
        pass


def create_job(user_id: str, conversion_type: str, original_name: str, source_size: int,
               source_path: Optional[str]) -> Optional[str]:
    """Sisipkan baris pekerjaan berstatus ``processing``; kembalikan id-nya."""
    client = get_client()

    if client is None:
        return None

    try:
        response = (
            client.table("jobs")
            .insert({
                "user_id": user_id,
                "conversion_type": conversion_type,
                "original_name": original_name,
                "source_size_bytes": source_size,
                "source_path": source_path,
                "status": "processing",
            })
            .execute()
        )
    except Exception:
        return None

    rows = getattr(response, "data", None) or []
    return rows[0]["id"] if rows else None


def finish_job(job_id: Optional[str], status: str, **fields) -> None:
    """Perbarui status + metadata pekerjaan (``completed`` / ``failed``)."""
    client = get_client()

    if client is None or not job_id:
        return

    payload = dict(fields)
    payload["status"] = status

    try:
        client.table("jobs").update(payload).eq("id", job_id).execute()
    except Exception:
        pass


def list_jobs(user_id: str, limit: int = 50) -> list:
    """Ambil riwayat pekerjaan terbaru milik pengguna."""
    client = get_client()

    if client is None:
        return []

    try:
        response = (
            client.table("jobs")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    except Exception:
        return []

    return getattr(response, "data", None) or []


def delete_job(user_id: str, job: dict) -> None:
    """Hapus berkas sumber/hasil lalu baris pekerjaan milik pengguna."""
    client = get_client()

    if client is None:
        return

    if job.get("source_path"):
        remove_objects(SOURCE_BUCKET, [job["source_path"]])

    if job.get("result_path"):
        remove_objects(RESULT_BUCKET, [job["result_path"]])

    try:
        client.table("jobs").delete().eq("id", job["id"]).eq("user_id", user_id).execute()
    except Exception:
        pass
