"""Siapkan database, table, dan bucket Appwrite untuk aplikasi Konverter.

Jalankan **sekali** dari root repo. Butuh API key Appwrite dengan scope database
(database/table/column/index) dan storage (bucket). API key hanya dipakai saat
setup -- jangan simpan di Streamlit Secrets dan hapus setelah selesai.

Contoh (Windows PowerShell):

    $env:APPWRITE_API_KEY = "standard_xxxx"
    python scripts/appwrite_setup.py --endpoint https://sgp.cloud.appwrite.io/v1 --project-id <PROJECT_ID>

Script ini idempotent: aman dijalankan berulang, objek yang sudah ada dilewati.
Di akhir, blok TOML untuk Streamlit Secrets dicetak ke layar.
"""

import argparse
import os
import sys

try:
    from appwrite.client import Client
    from appwrite.exception import AppwriteException
    from appwrite.permission import Permission
    from appwrite.role import Role
    from appwrite.services.storage import Storage
    from appwrite.services.tables_db import TablesDB
except ImportError:  # pragma: no cover - dipakai manual
    print("Paket 'appwrite' belum terpasang. Jalankan: pip install -r requirements.txt")
    raise SystemExit(1)

DEFAULT_ENDPOINT = "https://cloud.appwrite.io/v1"
DEFAULT_DATABASE = "konverter"
DEFAULT_TABLE = "jobs"
DEFAULT_SOURCES = "sources"
DEFAULT_RESULTS = "results"
MAX_FILE_MB = 50
ALLOWED_EXTENSIONS = ["pdf", "csv", "xlsx"]

# Kolom table `jobs` (mengikuti skema Supabase agar UI sama untuk kedua backend).
COLUMNS = [
    {"key": "user_id", "type": "varchar", "size": 36, "required": True},
    {"key": "conversion_type", "type": "varchar", "size": 32, "required": True},
    {"key": "original_name", "type": "varchar", "size": 255, "required": True},
    {"key": "source_path", "type": "varchar", "size": 512, "required": False},
    {"key": "source_size_bytes", "type": "bigint", "required": False},
    {"key": "result_name", "type": "varchar", "size": 255, "required": False},
    {"key": "result_path", "type": "varchar", "size": 512, "required": False},
    {"key": "output_size_bytes", "type": "bigint", "required": False},
    {"key": "status", "type": "varchar", "size": 16, "required": True, "default": "processing"},
    {"key": "error", "type": "text", "required": False},
    {"key": "page_count", "type": "integer", "required": False},
    {"key": "row_count", "type": "integer", "required": False},
    {"key": "duration_ms", "type": "integer", "required": False},
    {"key": "created_at", "type": "datetime", "required": True},
]

INDEXES = [
    {
        "key": "user_created",
        "type": "key",
        "attributes": ["user_id", "created_at"],
        "orders": ["asc", "desc"],
    },
]

# Table hanya boleh *dibuat baris* oleh user login. Izin baca/ubah/hapus
# diberikan per baris (Role.user) oleh adapter, sehingga data tiap pengguna
# terisolasi dan tidak bisa dibaca pengguna lain.
TABLE_PERMISSIONS = [Permission.create(Role.users())]
BUCKET_PERMISSIONS = [Permission.create(Role.users())]


def _conflict(exc: Exception) -> bool:
    """True bila error berarti objek sudah ada (HTTP 409)."""
    return getattr(exc, "code", 0) == 409


def _ensure(action: str, callback):
    """Jalankan pembuatan objek; lewati bila sudah ada."""
    try:
        result = callback()
        print(f"  + {action}: dibuat")
        return result
    except AppwriteException as exc:
        if _conflict(exc):
            print(f"  = {action}: sudah ada, dilewati")
            return None
        print(f"  ! {action}: gagal -> {exc}")
        raise


def build_client(endpoint: str, project_id: str, api_key: str):
    client = Client()
    client.set_endpoint(endpoint)
    client.set_project(project_id)
    client.set_key(api_key)
    return client


def setup(database_id: str, table_id: str, sources_id: str, results_id: str,
          client) -> None:
    tables = TablesDB(client)
    storage = Storage(client)

    print("1) Database")
    _ensure(f"database '{database_id}'", lambda: tables.create(database_id, "Konverter"))

    print("2) Table 'jobs' (kolom + index)")
    _ensure(
        f"table '{table_id}'",
        lambda: tables.create_table(
            database_id,
            table_id,
            "Jobs",
            permissions=TABLE_PERMISSIONS,
            row_security=True,
            enabled=True,
            columns=COLUMNS,
            indexes=INDEXES,
        ),
    )

    print("3) Bucket storage")
    for bucket_id, name in ((sources_id, "Sources"), (results_id, "Results")):
        _ensure(
            f"bucket '{bucket_id}'",
            lambda bucket_id=bucket_id, name=name: storage.create_bucket(
                bucket_id,
                name,
                permissions=BUCKET_PERMISSIONS,
                file_security=True,
                enabled=True,
                maximum_file_size=MAX_FILE_MB * 1024 * 1024,
                allowed_file_extensions=ALLOWED_EXTENSIONS,
            ),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Setup Appwrite untuk Konverter")
    parser.add_argument("--endpoint", default=os.environ.get("APPWRITE_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--project-id", default=os.environ.get("APPWRITE_PROJECT_ID"))
    parser.add_argument(
        "--api-key",
        default=os.environ.get("APPWRITE_API_KEY"),
        help="API key sekaligus (scope database + storage). Atau set env APPWRITE_API_KEY.",
    )
    parser.add_argument("--database-id", default=DEFAULT_DATABASE)
    parser.add_argument("--table-id", default=DEFAULT_TABLE)
    parser.add_argument("--sources-bucket", default=DEFAULT_SOURCES)
    parser.add_argument("--results-bucket", default=DEFAULT_RESULTS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.project_id:
        print("Project ID belum diisi. Pakai --project-id atau set env APPWRITE_PROJECT_ID.")
        return 2

    if not args.api_key:
        print("API key belum diisi. Pakai --api-key atau set env APPWRITE_API_KEY.")
        return 2

    print(f"Endpoint  : {args.endpoint}")
    print(f"Project ID: {args.project_id}")
    print()

    client = build_client(args.endpoint, args.project_id, args.api_key)

    try:
        setup(args.database_id, args.table_id, args.sources_bucket, args.results_bucket, client)
    except AppwriteException:
        return 1

    print()
    print("Selesai. Tempel blok berikut ke Streamlit Secrets (.streamlit/secrets.toml):")
    print()
    print("[appwrite]")
    print(f'endpoint = "{args.endpoint}"')
    print(f'project_id = "{args.project_id}"')
    print(f'database_id = "{args.database_id}"')
    print(f'jobs_table_id = "{args.table_id}"')
    print(f'sources_bucket_id = "{args.sources_bucket}"')
    print(f'results_bucket_id = "{args.results_bucket}"')
    print()
    print("[app]")
    print('backend = "appwrite"')
    print()
    print("Catatan: hapus API key ini setelah setup selesai.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
