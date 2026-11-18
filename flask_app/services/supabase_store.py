import logging
import os
import re
import time
from typing import Optional

try:
    import streamlit
except ImportError:
    streamlit = None

try:
    from supabase import Client, ClientOptions, create_client
except ImportError:
    Client = None
    ClientOptions = None
    create_client = None

SOURCE_BUCKET = "sources"
RESULT_BUCKET = "results"
SIGNED_URL_TTL = 3600

_CLIENT_KEY = "_sb_client"
_SESSION_KEY = "_sb_session"
_USER_KEY = "_sb_user"

logger = logging.getLogger(__name__)


def _secrets_section() -> dict:
    if streamlit is None:
        return {}

    try:
        section = streamlit.secrets.get("supabase")
    except Exception:
        return {}

    return dict(section) if section else {}


def _pick_secret_value(secret_value, env_name: str) -> str:
    value = secret_value.strip() if isinstance(secret_value, str) else ""

    if value and "<" not in value:
        return value

    return (os.environ.get(env_name) or "").strip()


def _config() -> dict:
    secrets = _secrets_section()
    return {
        "url": _pick_secret_value(secrets.get("url"), "SUPABASE_URL"),
        "anon_key": _pick_secret_value(secrets.get("anon_key"), "SUPABASE_ANON_KEY"),
    }


def is_configured() -> bool:
    if create_client is None:
        return False

    config = _config()
    if not config["url"] or not config["anon_key"]:
        return False

    return "<" not in config["url"] and "<" not in config["anon_key"]


def get_client() -> Optional["Client"]:
    if not is_configured() or streamlit is None:
        return None

    client = streamlit.session_state.get(_CLIENT_KEY)

    if client is None:
        config = _config()
        options = (
            ClientOptions(auto_refresh_token=False, persist_session=False)
            if ClientOptions is not None
            else None
        )

        try:
            client = create_client(config["url"], config["anon_key"], options)
        except Exception:
            return None

        streamlit.session_state[_CLIENT_KEY] = client
        _restore_session(client)

    return client


def _restore_session(client) -> None:
    session = streamlit.session_state.get(_SESSION_KEY)

    if not session:
        return

    try:
        client.auth.set_session(session["access_token"], session["refresh_token"])
    except Exception:
        streamlit.session_state.pop(_SESSION_KEY, None)
        streamlit.session_state.pop(_USER_KEY, None)


def _store_session_tokens() -> None:
    try:
        session = client_session()
        if session:
            streamlit.session_state[_SESSION_KEY] = {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
                "expires_at": int(getattr(session, "expires_at", 0) or 0),
            }
    except Exception:
        pass


def _remember_user(user) -> dict:
    if user is None:
        return current_user() or {}

    user_info = {"id": user.id, "email": getattr(user, "email", "") or ""}
    streamlit.session_state[_USER_KEY] = user_info
    _store_session_tokens()
    return user_info


def _clear_session() -> None:
    if streamlit is None:
        return

    for session_key in (_SESSION_KEY, _USER_KEY):
        streamlit.session_state.pop(session_key, None)


def _is_expired_session_error(error: Exception) -> bool:
    text = " ".join(
        [
            str(error),
            str(getattr(error, "code", "") or ""),
            str(getattr(error, "type", "") or ""),
        ]
    ).lower()

    return any(
        marker in text
        for marker in (
            "refresh token",
            "invalid_grant",
            "invalid refresh",
            "session_not_found",
            "jwt expired",
            "token has expired",
        )
    )


_client_for_session = None


def client_session():
    client = streamlit.session_state.get(_CLIENT_KEY)

    if client is None:
        return None

    try:
        return client.auth.get_session()
    except Exception:
        return None


def current_user() -> Optional[dict]:
    if streamlit is None:
        return None

    return streamlit.session_state.get(_USER_KEY)


def export_session() -> Optional[dict]:
    if streamlit is None:
        return None

    user = current_user()
    session = client_session() or streamlit.session_state.get(_SESSION_KEY)

    if not user or not session:
        return None

    access_token = getattr(session, "access_token", None)
    refresh_token = getattr(session, "refresh_token", None)

    if isinstance(session, dict):
        access_token = session.get("access_token")
        refresh_token = session.get("refresh_token")

    if not access_token or not refresh_token:
        return None

    return {
        "backend": "supabase",
        "v": 1,
        "user": user,
        "session": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": int(
                getattr(session, "expires_at", 0)
                or (session.get("expires_at") if isinstance(session, dict) else 0)
                or 0
            ),
        },
    }


def restore_session(payload: dict) -> Optional[dict]:
    if streamlit is None or not isinstance(payload, dict):
        return None

    if str(payload.get("backend") or "").lower() != "supabase":
        return None

    user = payload.get("user")
    session = payload.get("session")

    if not isinstance(user, dict) or not user.get("id"):
        return None

    if not isinstance(session, dict):
        return None

    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")

    if not access_token or not refresh_token:
        return None

    session_state = streamlit.session_state
    session_state.pop(_CLIENT_KEY, None)
    session_state[_USER_KEY] = {
        "id": user.get("id"),
        "email": user.get("email", "") or "",
    }
    session_state[_SESSION_KEY] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(session.get("expires_at") or 0),
    }

    if get_client() is None or client_session() is None:
        _clear_session()
        return None

    return ensure_session()


def ensure_session() -> Optional[dict]:
    if streamlit is None:
        return None

    client = get_client()
    user = current_user()

    if client is None or user is None:
        return user

    try:
        session = client.auth.get_session()
    except Exception as error:
        logger.exception("Gagal membaca sesi Supabase")

        if _is_expired_session_error(error):
            _clear_session()
            return None

        return user

    if session is None:
        _clear_session()
        return None

    expires_at = int(getattr(session, "expires_at", 0) or 0)

    if expires_at and expires_at - time.time() > 120:
        return user

    try:
        response = client.auth.refresh_session(session.refresh_token)
    except Exception as error:
        logger.exception("Gagal memperbarui access token Supabase")

        if _is_expired_session_error(error):
            _clear_session()
            return None

        return user

    _store_session_tokens()
    refreshed_user = getattr(response, "user", None)

    if refreshed_user is None:
        return user

    return _remember_user(refreshed_user)


def sign_up(email: str, password: str) -> dict:
    client = get_client()

    if client is None:
        raise RuntimeError("Supabase belum dikonfigurasi.")

    response = client.auth.sign_up({"email": email, "password": password})

    if getattr(response, "session", None):
        _remember_user(response.user)
        return {"needs_confirmation": False, "user": current_user()}

    return {"needs_confirmation": True, "user": None}


def sign_in(email: str, password: str) -> dict:
    client = get_client()

    if client is None:
        raise RuntimeError("Supabase belum dikonfigurasi.")

    response = client.auth.sign_in_with_password({"email": email, "password": password})
    return _remember_user(response.user)


def sign_out() -> None:
    if streamlit is None:
        return

    client = streamlit.session_state.get(_CLIENT_KEY)

    if client is not None:
        try:
            client.auth.sign_out()
        except Exception:
            pass

    for session_key in (_CLIENT_KEY, _SESSION_KEY, _USER_KEY):
        streamlit.session_state.pop(session_key, None)


def _safe_name(name: str) -> str:
    base_name = os.path.basename(name or "file")
    cleaned_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name).strip("._") or "file"
    return cleaned_name[:120]


def object_path(user_id: str, job_id: str, filename: str) -> str:
    return f"{user_id}/{job_id}/{_safe_name(filename)}"


def upload_file(bucket: str, path: str, data: bytes, content_type: str) -> None:
    client = get_client()

    if client is None:
        raise RuntimeError("Supabase belum dikonfigurasi.")

    client.storage.from_(bucket).upload(
        path,
        data,
        {"content-type": content_type or "application/octet-stream", "upsert": "true"},
    )


def signed_url(bucket: str, path: str, expires: int = SIGNED_URL_TTL) -> Optional[str]:
    client = get_client()

    if client is None:
        return None

    try:
        signed_url_result = client.storage.from_(bucket).create_signed_url(path, expires)
    except Exception:
        return None

    if isinstance(signed_url_result, dict):
        return signed_url_result.get("signedURL") or signed_url_result.get("signedUrl")

    return getattr(signed_url_result, "signed_url", None)


def remove_objects(bucket: str, paths) -> None:
    client = get_client()

    if client is None or not paths:
        return

    try:
        client.storage.from_(bucket).remove(list(paths))
    except Exception:
        pass


def create_job(user_id: str, conversion_type: str, original_name: str, source_size: int,
               source_path: Optional[str]) -> Optional[str]:
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

    row_list = getattr(response, "data", None) or []
    return row_list[0]["id"] if row_list else None


def finish_job(job_id: Optional[str], status: str, **fields) -> None:
    client = get_client()

    if client is None or not job_id:
        return

    update_payload = dict(fields)
    update_payload["status"] = status

    try:
        client.table("jobs").update(update_payload).eq("id", job_id).execute()
    except Exception:
        pass


def list_jobs(user_id: str, limit: int = 50) -> list:
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
