"""Siapkan database, table, dan bucket Appwrite untuk aplikasi Konverter.

Jalankan dari root repo. Butuh API key Appwrite dengan scope database
(database/table/column/index) dan storage (bucket). API key hanya dipakai saat
setup -- jangan simpan di Streamlit Secrets dan hapus setelah selesai.

Contoh (Windows PowerShell):

    $env:APPWRITE_API_KEY = "standard_xxxx"
    python scripts/appwrite_setup.py --endpoint https://sgp.cloud.appwrite.io/v1 --project-id <PROJECT_ID>

Script ini **idempotent**: tiap objek diperiksa lebih dulu lewat API *get*,
lalu dibuat bila belum ada atau diperbaiki bila sudah ada. Yang diperiksa:

- database ``konverter``
- table ``jobs`` beserta seluruh kolom dan index ``user_created``
- bucket ``sources`` (dan ``results`` bila plan mengizinkan)

**Plan Free Appwrite hanya mengizinkan 1 bucket.** Bila pembuatan bucket
``results`` ditolak karena kuota, script tidak berhenti: hasil konversi
otomatis diarahkan ke bucket ``sources`` (satu bucket untuk sumber + hasil)
dan blok TOML yang dicetak sudah memakai ``results_bucket_id = "sources"``.
Pemisahan tetap aman karena path file memakai ``<user_id>/<job_id>/<nama>``
dan izin diberikan per file oleh aplikasi.

Perbedaan penting dari versi lama: error kuota plan **tidak lagi** dilaporkan
sebagai "sudah ada", dan bila setup gagal script mencetak pesan error Appwrite
yang sebenarnya (bukan diam-diam berhenti).
"""

import argparse
import os
import sys

try:
    from appwrite.client import Client
    from appwrite.enums.order_by import OrderBy
    from appwrite.enums.tables_db_index_type import TablesDBIndexType
    from appwrite.exception import AppwriteException
    from appwrite.permission import Permission
    from appwrite.query import Query
    from appwrite.role import Role
    from appwrite.services.storage import Storage
    from appwrite.services.tables_db import TablesDB
except ImportError:
    print("Paket 'appwrite' belum terpasang. Jalankan: pip install -r requirements.txt")
    raise SystemExit(1)

DEFAULT_ENDPOINT = "https://cloud.appwrite.io/v1"
DEFAULT_DATABASE = "konverter"
DEFAULT_DATABASE_NAME = "Konverter"
DEFAULT_TABLE = "jobs"
DEFAULT_TABLE_NAME = "Jobs"
DEFAULT_SOURCES = "sources"
DEFAULT_RESULTS = "results"
MAX_FILE_BYTES = 50_000_000
ALLOWED_EXTENSIONS = ["pdf", "csv", "xlsx"]

COLUMNS = [
    {"key": "user_id", "type": "varchar", "size": 36, "required": True},
    {"key": "conversion_type", "type": "varchar", "size": 32, "required": True},
    {"key": "original_name", "type": "varchar", "size": 255, "required": True},
    {"key": "source_path", "type": "varchar", "size": 512, "required": False},
    {"key": "source_size_bytes", "type": "bigint", "required": False},
    {"key": "result_name", "type": "varchar", "size": 255, "required": False},
    {"key": "result_path", "type": "varchar", "size": 512, "required": False},
    {"key": "output_size_bytes", "type": "bigint", "required": False},
    {"key": "status", "type": "varchar", "size": 16, "required": False, "default": "processing"},
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
        "orders": ["ASC", "DESC"],
    },
]

QUOTA_MARKERS = (
    "maximum number of",
    "has reached the limit",
    "reached the limit",
    "upgrade your plan",
    "plan limit",
)

TABLE_PERMISSIONS = [Permission.create(Role.users())]
BUCKET_PERMISSIONS = [Permission.create(Role.users())]


def _message(exc: Exception) -> str:
    """Teks pesan error Appwrite (fallback ke str)."""
    return str(getattr(exc, "message", None) or exc or "")


def _code(exc: Exception) -> int:
    """Kode HTTP error Appwrite (0 bila tidak ada)."""
    return int(getattr(exc, "code", 0) or 0)


def _conflict(exc: Exception) -> bool:
    """True bila error berarti objek sudah ada."""
    if _code(exc) == 409:
        return True

    low = _message(exc).lower()
    return "already exists" in low or "already been created" in low


def _quota_error(exc: Exception) -> bool:
    """True bila error karena kuota plan (bukan karena objek sudah ada)."""
    low = _message(exc).lower()
    return any(marker in low for marker in QUOTA_MARKERS)


def _exists(getter) -> bool:
    """True bila objek sudah ada.

    ``getter`` memanggil endpoint *get*. Appwrite mengembalikan HTTP 404 bila
    belum ada. Error lain (401, 403, jaringan) sengaja diteruskan supaya
    terlihat oleh pemanggil -- bukan disamarkan sebagai "sudah ada".
    """
    try:
        getter()
        return True
    except AppwriteException as exc:
        if _code(exc) == 404:
            return False
        raise


def _ensure(action: str, callback):
    """Jalankan pembuatan objek; lewati bila ternyata sudah ada."""
    try:
        result = callback()
        print(f"  + {action}: dibuat")
        return result
    except AppwriteException as exc:
        if _conflict(exc):
            print(f"  = {action}: sudah ada, dilewati")
            return None
        print(f"  ! {action}: gagal -> {_message(exc)}")
        raise


def _create_column(tables: TablesDB, database_id: str, table_id: str, column: dict):
    """Buat satu kolom sesuai tipe di ``COLUMNS``."""
    key = column["key"]
    required = bool(column.get("required", False))
    default = column.get("default")
    kind = column["type"]

    if kind == "varchar":
        return tables.create_varchar_column(
            database_id, table_id, key, float(column.get("size", 255)), required, default=default
        )
    if kind == "bigint":
        return tables.create_big_int_column(database_id, table_id, key, required, default=default)
    if kind == "integer":
        return tables.create_integer_column(database_id, table_id, key, required, default=default)
    if kind == "text":
        return tables.create_text_column(database_id, table_id, key, required, default=default)
    if kind == "datetime":
        return tables.create_datetime_column(database_id, table_id, key, required, default=default)

    raise RuntimeError(f"Tipe kolom '{kind}' belum didukung skrip (kolom '{key}').")


def _ensure_columns(tables: TablesDB, database_id: str, table_id: str) -> None:
    """Pastikan semua kolom di ``COLUMNS`` ada (tambahkan yang belum ada)."""
    result = tables.list_columns(database_id, table_id, queries=[Query.limit(100)])
    existing = {getattr(column, "key", None) for column in result.columns}

    for column in COLUMNS:
        if column["key"] in existing:
            continue

        _ensure(
            f"kolom '{column['key']}'",
            lambda column=column: _create_column(tables, database_id, table_id, column),
        )


def _ensure_indexes(tables: TablesDB, database_id: str, table_id: str) -> None:
    """Pastikan semua index ada.

    Dipisah dari ``create_table`` agar tabel yang sudah ada tanpa index
    (mis. dibuat manual, atau ``create_table`` sempat gagal) tetap diperbaiki.
    """
    for index in INDEXES:
        key = index["key"]
        if _exists(lambda key=key: tables.get_index(database_id, table_id, key)):
            print(f"  = index '{key}': sudah ada, dilewati")
            continue

        orders = [OrderBy.ASC if order.upper() == "ASC" else OrderBy.DESC for order in index["orders"]]
        _ensure(
            f"index '{key}'",
            lambda index=index, orders=orders: tables.create_index(
                database_id,
                table_id,
                index["key"],
                TablesDBIndexType.KEY,
                index["attributes"],
                orders=orders,
            ),
        )


def _bucket_diffs(bucket) -> list:
    """Daftar pengaturan bucket yang tidak sesuai kebutuhan aplikasi."""
    diffs = []

    if getattr(bucket, "filesecurity", False) is not True:
        diffs.append("file security harus ON")
    if getattr(bucket, "enabled", False) is not True:
        diffs.append("bucket harus enabled")
    if int(getattr(bucket, "maximumfilesize", 0) or 0) != MAX_FILE_BYTES:
        diffs.append(f"batas ukuran harus {MAX_FILE_BYTES} byte")
    if sorted(getattr(bucket, "allowedfileextensions", None) or []) != sorted(ALLOWED_EXTENSIONS):
        diffs.append("ekstensi harus " + ", ".join(ALLOWED_EXTENSIONS))
    if sorted(getattr(bucket, "permissions", None) or []) != sorted(BUCKET_PERMISSIONS):
        diffs.append('izin bucket harus create("users")')

    return diffs


def _ensure_bucket(storage: Storage, bucket_id: str, name: str) -> bool:
    """Buat bucket atau samakan pengaturannya.

    Mengembalikan ``False`` bila bucket tidak bisa dibuat karena kuota plan
    (bukan melempar exception), supaya pemanggil bisa beralih ke satu bucket.
    """
    try:
        bucket = storage.get_bucket(bucket_id)
    except AppwriteException as exc:
        if _code(exc) != 404:
            print(f"  ! bucket '{bucket_id}': gagal diperiksa -> {_message(exc)}")
            raise
        bucket = None

    if bucket is None:
        try:
            storage.create_bucket(
                bucket_id,
                name,
                permissions=BUCKET_PERMISSIONS,
                file_security=True,
                enabled=True,
                maximum_file_size=MAX_FILE_BYTES,
                allowed_file_extensions=ALLOWED_EXTENSIONS,
            )
        except AppwriteException as exc:
            if _conflict(exc):
                print(f"  = bucket '{bucket_id}': sudah ada, dilewati")
                return True
            if _quota_error(exc):
                print(f"  ! bucket '{bucket_id}': tidak bisa dibuat -- kuota plan habis")
                print(f"    {_message(exc)}")
                return False
            print(f"  ! bucket '{bucket_id}': gagal -> {_message(exc)}")
            raise

        print(f"  + bucket '{bucket_id}': dibuat")
        return True

    diffs = _bucket_diffs(bucket)

    if not diffs:
        print(f"  = bucket '{bucket_id}': sudah ada, pengaturan sesuai")
        return True

    storage.update_bucket(
        bucket_id,
        name,
        permissions=BUCKET_PERMISSIONS,
        file_security=True,
        enabled=True,
        maximum_file_size=MAX_FILE_BYTES,
        allowed_file_extensions=ALLOWED_EXTENSIONS,
    )
    print(f"  ~ bucket '{bucket_id}': pengaturan disamakan ({'; '.join(diffs)})")
    return True


def build_client(endpoint: str, project_id: str, api_key: str):
    client = Client()
    client.set_endpoint(endpoint)
    client.set_project(project_id)
    client.set_key(api_key)
    return client


def setup(database_id: str, table_id: str, sources_id: str, results_id: str,
          client) -> str:
    """Siapkan semua resource. Mengembalikan bucket hasil yang benar-benar dipakai."""
    tables = TablesDB(client)
    storage = Storage(client)

    print("1) Database")
    if _exists(lambda: tables.get(database_id)):
        print(f"  = database '{database_id}': sudah ada, dilewati")
    else:
        _ensure(
            f"database '{database_id}'",
            lambda: tables.create(database_id, DEFAULT_DATABASE_NAME),
        )

    print(f"2) Table '{table_id}' (kolom + index)")
    if _exists(lambda: tables.get_table(database_id, table_id)):
        print(f"  = table '{table_id}': sudah ada, dilewati")
    else:
        _ensure(
            f"table '{table_id}'",
            lambda: tables.create_table(
                database_id,
                table_id,
                DEFAULT_TABLE_NAME,
                permissions=TABLE_PERMISSIONS,
                row_security=True,
                enabled=True,
                columns=COLUMNS,
                indexes=INDEXES,
            ),
        )

    _ensure_columns(tables, database_id, table_id)
    _ensure_indexes(tables, database_id, table_id)

    print("3) Bucket storage")
    if not _ensure_bucket(storage, sources_id, "Sources"):
        raise RuntimeError(
            f"Bucket '{sources_id}' tidak tersedia dan tidak bisa dibuat. "
            "Kosongkan salah satu bucket di Appwrite Console lalu jalankan ulang."
        )

    if results_id == sources_id:
        print(f"  = bucket '{results_id}': dipakai untuk sumber sekaligus hasil")
        return results_id

    if _ensure_bucket(storage, results_id, "Results"):
        return results_id

    print()
    print(f"  -> hasil konversi akan disimpan di bucket '{sources_id}'")
    print("     (satu bucket untuk sumber + hasil; path dan izin tetap terpisah per file)")
    return sources_id


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
    parser.add_argument(
        "--results-bucket",
        default=DEFAULT_RESULTS,
        help=(
            "Bucket hasil. Pada plan Free (1 bucket) isi dengan nama bucket sumber, "
            "mis. --results-bucket sources; script juga otomatis beralih bila kuota habis."
        ),
    )
    return parser.parse_args()


def print_toml(args: argparse.Namespace, results_bucket: str) -> None:
    print()
    print("Selesai. Tempel blok berikut ke Streamlit Secrets (.streamlit/secrets.toml):")
    print()
    print("[appwrite]")
    print(f'endpoint = "{args.endpoint}"')
    print(f'project_id = "{args.project_id}"')
    print(f'database_id = "{args.database_id}"')
    print(f'jobs_table_id = "{args.table_id}"')
    print(f'sources_bucket_id = "{args.sources_bucket}"')
    print(f'results_bucket_id = "{results_bucket}"')
    print()
    print("[app]")
    print('backend = "appwrite"')
    print()

    if results_bucket == args.sources_bucket:
        print(
            "Catatan: satu bucket dipakai untuk sumber dan hasil (batas plan Free). "
            "File tetap terpisah lewat path <user_id>/<job_id>/<nama-file>."
        )

    print("Catatan: hapus API key ini setelah setup selesai.")


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
        results_bucket = setup(
            args.database_id, args.table_id, args.sources_bucket, args.results_bucket, client
        )
    except AppwriteException as exc:
        print()
        print(f"GAGAL: {_message(exc)}")
        if _code(exc):
            print(f"Kode  : {_code(exc)}")
        error_type = getattr(exc, "type", None)
        if error_type:
            print(f"Tipe  : {error_type}")
        if _code(exc) in (401, 403):
            print()
            print("Petunjuk: pastikan API key dibuat di project yang sama dengan --project-id,")
            print("dan punya scope databases + storage. API key tidak bisa dipakai lintas project.")
        return 1
    except RuntimeError as exc:
        print()
        print(f"GAGAL: {exc}")
        return 1

    print_toml(args, results_bucket)
    return 0


if __name__ == "__main__":
    sys.exit(main())
