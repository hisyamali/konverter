import base64
import os
import re
from functools import wraps
from pathlib import Path
from flask import (
    Blueprint, current_app, render_template, request,
    jsonify, send_file, abort, Response
)

logs_blueprint = Blueprint("logs", __name__, url_prefix="/logs")


def _get_log_directory():
    root = Path(current_app.root_path).parent
    return root / "storage" / "logs"


def _check_authentication():
    configured_credentials = os.environ.get("LOG_VIEWER_AUTH")
    if not configured_credentials:
        return True

    expected = configured_credentials.strip()
    authorization_header = request.headers.get("Authorization", "")

    if not authorization_header.startswith("Basic "):
        return False

    try:
        decoded_credentials = base64.b64decode(authorization_header[6:]).decode("utf-8")
        return decoded_credentials == expected
    except Exception:
        return False


def _require_authentication():
    def decorator(view_function):
        @wraps(view_function)
        def wrapped(*args, **kwargs):
            if not _check_authentication():
                return Response(
                    "Authentication required",
                    401,
                    {"WWW-Authenticate": 'Basic realm="Log Viewer"'}
                )
            return view_function(*args, **kwargs)
        return wrapped
    return decorator


def _list_log_files():
    log_directory = _get_log_directory()
    if not log_directory.exists():
        return []

    files = []
    for log_file in log_directory.iterdir():
        if log_file.is_file() and log_file.suffix in (".log", ""):
            file_stat = log_file.stat()
            files.append({
                "name": log_file.name,
                "size": file_stat.st_size,
                "size_human": _human_size(file_stat.st_size),
                "mtime": file_stat.st_mtime,
                "mtime_str": _format_modification_time(file_stat.st_mtime),
            })
    files.sort(key=lambda file_info: file_info["mtime"], reverse=True)
    return files


def _human_size(size_bytes):
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _format_modification_time(modification_time):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    datetime_value = datetime.fromtimestamp(modification_time, ZoneInfo("Asia/Jakarta"))
    return datetime_value.strftime("%Y-%m-%d %H:%M:%S")


def _read_log_tail(filepath, lines=500, level_filter=None, search=None):
    if not filepath.exists():
        return [], 0

    level_map = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    min_level = level_map.get(level_filter, -1) if level_filter else -1

    from collections import deque
    matched = deque(maxlen=lines)
    total_lines = 0

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as log_file_handle:
            for line in log_file_handle:
                total_lines += 1
                if search and search.lower() not in line.lower():
                    continue
                if min_level >= 0:
                    level_match = re.search(r'\]\s+\w+\.(\w+):', line)
                    if level_match:
                        line_level = level_match.group(1).upper()
                        if level_map.get(line_level, -1) < min_level:
                            continue
                matched.append(line.rstrip("\n"))
    except Exception:
        pass

    return list(matched), total_lines


@logs_blueprint.before_request
def _enforce_authentication():
    if not _check_authentication():
        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="Log Viewer"'}
        )


@logs_blueprint.get("/")
def index():
    files = _list_log_files()
    return render_template("logs/index.html", files=files)


@logs_blueprint.get("/view/<path:filename>")
def view(filename):
    log_directory = _get_log_directory()
    filepath = log_directory / filename

    try:
        filepath.resolve().relative_to(log_directory.resolve())
    except ValueError:
        abort(404)

    if not filepath.exists():
        abort(404)

    lines = request.args.get("lines", default=500, type=int)
    lines = max(10, min(lines, 5000))
    level = request.args.get("level", default="", type=str).upper()
    search = request.args.get("search", default="", type=str)
    auto_refresh = request.args.get("auto", default="false").lower() in ("true", "1", "on", "yes")

    log_lines, total_line_count = _read_log_tail(filepath, lines, level if level else None, search if search else None)

    return render_template("logs/view.html",
                           filename=filename,
                           log_lines=log_lines,
                           total_lines=total_line_count,
                           lines=lines,
                           level=level,
                           search=search,
                           auto_refresh=auto_refresh,
                           levels=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])


@logs_blueprint.get("/api")
def api():
    filename = request.args.get("file", default="app.log", type=str)
    lines = request.args.get("lines", default=200, type=int)
    lines = max(10, min(lines, 2000))
    level = request.args.get("level", default="", type=str).upper()
    search = request.args.get("search", default="", type=str)
    offset = request.args.get("offset", default=0, type=int)

    log_directory = _get_log_directory()
    filepath = log_directory / filename

    try:
        filepath.resolve().relative_to(log_directory.resolve())
    except ValueError:
        return jsonify({"error": "Invalid file"}), 400

    if not filepath.exists():
        return jsonify({"error": "File not found"}), 404

    log_lines, total_line_count = _read_log_tail(filepath, lines + offset, level if level else None, search if search else None)

    page_lines = log_lines[offset:offset + lines]

    return jsonify({
        "lines": page_lines,
        "total": total_line_count,
        "offset": offset,
        "returned": len(page_lines),
    })


@logs_blueprint.get("/download/<path:filename>")
def download(filename):
    log_directory = _get_log_directory()
    filepath = log_directory / filename

    try:
        filepath.resolve().relative_to(log_directory.resolve())
    except ValueError:
        abort(404)

    if not filepath.exists():
        abort(404)

    return send_file(filepath, as_attachment=True, download_name=filename)
