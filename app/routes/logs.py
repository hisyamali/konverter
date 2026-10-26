"""Blueprint untuk Log Viewer: akses log dengan autentikasi basic auth."""

import base64
import os
import re
from functools import wraps
from pathlib import Path
from flask import (
    Blueprint, current_app, render_template, request,
    jsonify, send_file, abort, Response
)

logs_bp = Blueprint("logs", __name__, url_prefix="/logs")


def _get_log_dir():
    """Direktori log: storage/logs/ relatif terhadap root proyek."""
    root = Path(current_app.root_path).parent
    return root / "storage" / "logs"


def _check_auth():
    """Cek basic auth jika LOG_VIEWER_AUTH diset."""
    auth = os.environ.get("LOG_VIEWER_AUTH")
    if not auth:
        return True  # Tidak ada auth → izinkan (dev mode)

    expected = auth.strip()
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Basic "):
        return False

    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        return decoded == expected
    except Exception:
        return False


def _require_auth():
    """Decorator untuk require basic auth."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not _check_auth():
                return Response(
                    "Authentication required",
                    401,
                    {"WWW-Authenticate": 'Basic realm="Log Viewer"'}
                )
            return f(*args, **kwargs)
        return wrapped
    return decorator


def _list_log_files():
    """Dapatkan daftar file log di storage/logs/, terurut terbaru dulu."""
    log_dir = _get_log_dir()
    if not log_dir.exists():
        return []

    files = []
    for f in log_dir.iterdir():
        if f.is_file() and f.suffix in (".log", ""):
            stat = f.stat()
            files.append({
                "name": f.name,
                "size": stat.st_size,
                "size_human": _human_size(stat.st_size),
                "mtime": stat.st_mtime,
                "mtime_str": _format_mtime(stat.st_mtime),
            })
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files


def _human_size(size_bytes):
    """Format ukuran file human-readable."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _format_mtime(mtime):
    """Format mtime ke string Y-m-d H:i:s."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    dt = datetime.fromtimestamp(mtime, ZoneInfo("Asia/Jakarta"))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _read_log_tail(filepath, lines=500, level_filter=None, search=None):
    """Baca tail N baris dari file log dengan filter level dan search."""
    if not filepath.exists():
        return [], 0

    level_map = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    min_level = level_map.get(level_filter, -1) if level_filter else -1

    # Baca dari belakang (efficient untuk file besar)
    # Gunakan deque untuk tail efisien
    from collections import deque
    matched = deque(maxlen=lines)
    total_lines = 0

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                total_lines += 1
                if search and search.lower() not in line.lower():
                    continue
                if min_level >= 0:
                    # Cek level di awal baris: [timestamp] channel.LEVEL:
                    match = re.search(r'\]\s+\w+\.(\w+):', line)
                    if match:
                        line_level = match.group(1).upper()
                        if level_map.get(line_level, -1) < min_level:
                            continue
                matched.append(line.rstrip("\n"))
    except Exception:
        pass

    return list(matched), total_lines


@logs_bp.before_request
def _enforce_auth():
    if not _check_auth():
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="Log Viewer"'}
        )


@logs_bp.get("/")
def index():
    """Daftar file log."""
    files = _list_log_files()
    return render_template("logs/index.html", files=files)


@logs_bp.get("/view/<path:filename>")
def view(filename):
    """Tampilan log dengan filter, tail, search."""
    log_dir = _get_log_dir()
    filepath = log_dir / filename

    # Keamanan: hindari path traversal
    try:
        filepath.resolve().relative_to(log_dir.resolve())
    except ValueError:
        abort(404)

    if not filepath.exists():
        abort(404)

    # Query params
    lines = request.args.get("lines", default=500, type=int)
    lines = max(10, min(lines, 5000))
    level = request.args.get("level", default="", type=str).upper()
    search = request.args.get("search", default="", type=str)
    auto_refresh = request.args.get("auto", default="false").lower() in ("true", "1", "on", "yes")

    log_lines, total = _read_log_tail(filepath, lines, level if level else None, search if search else None)

    return render_template("logs/view.html",
                           filename=filename,
                           log_lines=log_lines,
                           total_lines=total,
                           lines=lines,
                           level=level,
                           search=search,
                           auto_refresh=auto_refresh,
                           levels=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])


@logs_bp.get("/api")
def api():
    """API untuk AJAX: filter, tail, search → JSON lines."""
    filename = request.args.get("file", default="app.log", type=str)
    lines = request.args.get("lines", default=200, type=int)
    lines = max(10, min(lines, 2000))
    level = request.args.get("level", default="", type=str).upper()
    search = request.args.get("search", default="", type=str)
    offset = request.args.get("offset", default=0, type=int)

    log_dir = _get_log_dir()
    filepath = log_dir / filename

    try:
        filepath.resolve().relative_to(log_dir.resolve())
    except ValueError:
        return jsonify({"error": "Invalid file"}), 400

    if not filepath.exists():
        return jsonify({"error": "File not found"}), 404

    log_lines, total = _read_log_tail(filepath, lines + offset, level if level else None, search if search else None)

    # Pagination: ambil slice
    page_lines = log_lines[offset:offset + lines]

    return jsonify({
        "lines": page_lines,
        "total": total,
        "offset": offset,
        "returned": len(page_lines),
    })


@logs_bp.get("/download/<path:filename>")
def download(filename):
    """Download file log asli."""
    log_dir = _get_log_dir()
    filepath = log_dir / filename

    try:
        filepath.resolve().relative_to(log_dir.resolve())
    except ValueError:
        abort(404)

    if not filepath.exists():
        abort(404)

    return send_file(filepath, as_attachment=True, download_name=filename)