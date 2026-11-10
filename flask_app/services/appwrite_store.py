import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

try:
    import requests
except ImportError:
    requests = None

try:
    import streamlit
except ImportError:
    streamlit = None

try:
    from appwrite.client import Client
    from appwrite.exception import AppwriteException
    from appwrite.id import ID
    from appwrite.input_file import InputFile
    from appwrite.permission import Permission
    from appwrite.query import Query
    from appwrite.role import Role
    from appwrite.services.account import Account
    from appwrite.services.storage import Storage
    from appwrite.services.tables_db import TablesDB
except ImportError:
    Client = None
    AppwriteException = Exception
    ID = None
    InputFile = None
    Permission = None
    Query = None
    Role = None
    Account = None
    Storage = None
    TablesDB = None

SOURCE_BUCKET = "sources"
RESULT_BUCKET = "results"

_CLIENT_KEY = "_aw_client"
_SESSION_KEY = "_aw_session"
_USER_KEY = "_aw_user"

_REQUIRED_MESSAGE = "Appwrite belum dikonfigurasi."


def _secrets_section() -> dict:
    if streamlit is None:
        return {}

    try:
        section = streamlit.secrets.get("appwrite")
    except Exception:
        return {}

    return dict(section) if section else {}


def _pick_secret_value(secret_value, env_name: str, default: str = "") -> str:
    value = secret_value.strip() if isinstance(secret_value, str) else ""

    if value and "<" not in value:
        return value

    return (os.environ.get(env_name) or default).strip()


def _config() -> dict:
    secrets = _secrets_section()
    return {
        "endpoint": _pick_secret_value(secrets.get("endpoint"), "APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1"),
        "project_id": _pick_secret_value(secrets.get("project_id"), "APPWRITE_PROJECT_ID"),
        "database_id": _pick_secret_value(secrets.get("database_id"), "APPWRITE_DATABASE_ID", "konverter"),
        "jobs_table_id": _pick_secret_value(secrets.get("jobs_table_id"), "APPWRITE_JOBS_TABLE_ID", "jobs"),
        "sources_bucket_id": _pick_secret_value(secrets.get("sources_bucket_id"), "APPWRITE_SOURCES_BUCKET_ID", "sources"),
        "results_bucket_id": _pick_secret_value(secrets.get("results_bucket_id"), "APPWRITE_RESULTS_BUCKET_ID", "sources"),
    }


def is_configured() -> bool:
    if Client is None:
        return False

    config = _config()
    return bool(config["endpoint"]) and bool(config["project_id"]) and "<" not in config["project_id"]


def get_client() -> Optional["Client"]:
    if not is_configured() or streamlit is None:
        return None

    client = streamlit.session_state.get(_CLIENT_KEY)

    if client is None:
        config = _config()
        try:
            client = Client()
            client.set_endpoint(config["endpoint"])
            client.set_project(config["project_id"])
        except Exception:
            return None

        streamlit.session_state[_CLIENT_KEY] = client
        _restore_session(client)

    return client


def _require_client() -> "Client":
    client = get_client()

    if client is None:
        raise RuntimeError(_REQUIRED_MESSAGE)

    return client


def _cookie_from_response(response) -> Optional[str]:
    fallback = response.headers.get("x-fallback-cookies")

    if fallback:
        try:
            cookies = json.loads(fallback)
        except (TypeError, ValueError):
            cookies = {}

        parts = [f"{name}={value}" for name, value in cookies.items() if name and value]

        if parts:
            return "; ".join(parts)

    parts = [f"{cookie.name}={cookie.value}" for cookie in response.cookies if cookie.name.startswith("a_session_")]

    return "; ".join(parts) or None


def _create_email_password_session(client, email: str, password: str) -> str:
    if requests is None:
        raise AppwriteException("Pustaka requests tidak tersedia untuk login Appwrite.")

    config = _config()
    response_format = client.get_headers().get("X-Appwrite-Response-Format") or "2.6.2"
    headers = {
        "X-Appwrite-Project": config["project_id"],
        "X-Appwrite-Response-Format": response_format,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = requests.post(
            config["endpoint"].rstrip("/") + "/account/sessions/email",
            headers=headers,
            json={"email": email, "password": password},
            timeout=20,
        )
    except Exception as error:
        raise AppwriteException(f"Server akun tidak dapat dihubungi: {error}")

    if response.status_code >= 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        raise AppwriteException(
            payload.get("message") or response.text or "Login gagal.",
            payload.get("code", response.status_code),
            payload.get("type"),
        )

    cookie = _cookie_from_response(response)

    if not cookie:
        raise AppwriteException("Sesi Appwrite tidak mengembalikan cookie autentikasi.")

    client.set_cookie(cookie)
    return cookie


def _restore_session(client) -> None:
    session = streamlit.session_state.get(_SESSION_KEY)

    if not session:
        return

    cookie = session.get("cookie") if isinstance(session, dict) else None

    if not cookie:
        streamlit.session_state.pop(_SESSION_KEY, None)
        streamlit.session_state.pop(_USER_KEY, None)
        return

    try:
        client.set_cookie(cookie)
    except Exception:
        streamlit.session_state.pop(_SESSION_KEY, None)
        streamlit.session_state.pop(_USER_KEY, None)


def _remember_user(user, cookie: Optional[str] = None) -> dict:
    user_info = {
        "id": getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else "") or "",
        "email": getattr(user, "email", None) or (user.get("email") if isinstance(user, dict) else "") or "",
    }
    streamlit.session_state[_USER_KEY] = user_info

    if cookie:
        streamlit.session_state[_SESSION_KEY] = {"cookie": cookie, "user_id": user_info["id"]}

    return user_info


def current_user() -> Optional[dict]:
    if streamlit is None:
        return None

    return streamlit.session_state.get(_USER_KEY)


def sign_up(email: str, password: str) -> dict:
    client = _require_client()
    account = Account(client)
    name = (email.split("@")[0] or "pengguna")[:120]

    account.create(ID.unique(), email, password, name)
    cookie = _create_email_password_session(client, email, password)
    user = account.get()
    _remember_user(user, cookie)

    return {"needs_confirmation": False, "user": current_user()}


def sign_in(email: str, password: str) -> dict:
    client = _require_client()
    account = Account(client)

    cookie = _create_email_password_session(client, email, password)
    user = account.get()

    return _remember_user(user, cookie)


def sign_out() -> None:
    if streamlit is None:
        return

    client = streamlit.session_state.get(_CLIENT_KEY)

    if client is not None:
        try:
            Account(client).delete_session("current")
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


def _file_id(path: str) -> str:
    return hashlib.sha256((path or "").encode("utf-8")).hexdigest()[:32]


def _bucket_id(bucket: str) -> str:
    config = _config()
    return config["results_bucket_id"] if bucket == RESULT_BUCKET else config["sources_bucket_id"]


def _owner_permissions():
    user = current_user() or {}
    user_id = user.get("id")

    if not user_id or Permission is None:
        return None

    return [
        Permission.read(Role.user(user_id)),
        Permission.update(Role.user(user_id)),
        Permission.delete(Role.user(user_id)),
    ]


def upload_file(bucket: str, path: str, data: bytes, content_type: str) -> None:
    client = _require_client()
    storage = Storage(client)
    bucket_id = _bucket_id(bucket)
    file_id = _file_id(path)
    filename = os.path.basename(path or "file")
    permissions = _owner_permissions()

    def _create():
        storage.create_file(
            bucket_id,
            file_id,
            InputFile.from_bytes(data, filename, content_type or "application/octet-stream"),
            permissions=permissions,
        )

    try:
        _create()
    except AppwriteException as error:
        if getattr(error, "code", 0) == 409:
            try:
                storage.delete_file(bucket_id, file_id)
            except Exception:
                pass
            _create()
        else:
            raise


def signed_url(bucket: str, path: str, expires: int = 3600) -> Optional[str]:
    return None


def download_bytes(bucket: str, path: str) -> Optional[bytes]:
    client = get_client()

    if client is None:
        return None

    try:
        return Storage(client).get_file_download(_bucket_id(bucket), _file_id(path))
    except Exception:
        return None


def remove_objects(bucket: str, paths) -> None:
    client = get_client()
    paths = [path for path in (paths or []) if path]

    if client is None or not paths:
        return

    storage = Storage(client)
    bucket_id = _bucket_id(bucket)

    for path in paths:
        try:
            storage.delete_file(bucket_id, _file_id(path))
        except Exception:
            pass


def _table():
    client = get_client()

    if client is None:
        return None, None, None

    config = _config()
    return TablesDB(client), config["database_id"], config["jobs_table_id"]


def _row_to_job(row) -> dict:
    data = getattr(row, "data", None) or {}
    job_record = dict(data)

    row_id = getattr(row, "id", None)
    if row_id:
        job_record["id"] = row_id

    created = getattr(row, "createdat", None)
    if created and not job_record.get("created_at"):
        job_record["created_at"] = created

    updated = getattr(row, "updatedat", None)
    if updated:
        job_record["updated_at"] = updated

    return job_record


def create_job(user_id: str, conversion_type: str, original_name: str, source_size: int,
               source_path: Optional[str]) -> Optional[str]:
    table, database_id, table_id = _table()

    if table is None:
        return None

    row_payload = {
        "user_id": user_id,
        "conversion_type": conversion_type,
        "original_name": original_name,
        "source_size_bytes": int(source_size or 0),
        "source_path": source_path,
        "status": "processing",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    row_payload = {session_key: value for session_key, value in row_payload.items() if value is not None}

    try:
        row = table.create_row(database_id, table_id, ID.unique(), row_payload,
                               permissions=_owner_permissions())
    except Exception:
        return None

    return getattr(row, "id", None) or None


def finish_job(job_id: Optional[str], status: str, **fields) -> None:
    table, database_id, table_id = _table()

    if table is None or not job_id:
        return

    row_payload = dict(fields)
    row_payload["status"] = status

    try:
        table.update_row(database_id, table_id, job_id, row_payload)
    except Exception:
        pass


def list_jobs(user_id: str, limit: int = 50) -> list:
    table, database_id, table_id = _table()

    if table is None:
        return []

    try:
        response = table.list_rows(
            database_id,
            table_id,
            queries=[
                Query.equal("user_id", user_id),
                Query.order_desc("created_at"),
                Query.limit(limit),
            ],
        )
    except Exception:
        return []

    row_list = getattr(response, "rows", None) or []
    return [_row_to_job(row) for row in row_list]


def delete_job(user_id: str, job_record: dict) -> None:
    if job_record.get("source_path"):
        remove_objects(SOURCE_BUCKET, [job_record["source_path"]])

    if job_record.get("result_path"):
        remove_objects(RESULT_BUCKET, [job_record["result_path"]])

    table, database_id, table_id = _table()

    if table is None or not job_record.get("id"):
        return

    try:
        table.delete_row(database_id, table_id, job_record["id"])
    except Exception:
        pass
