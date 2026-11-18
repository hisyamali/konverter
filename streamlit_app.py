import base64
import html
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

os.environ.setdefault("KONVERTER_MAX_WORKERS", "4" if os.name == "nt" else "2")
os.environ.setdefault("KONVERTER_MAX_UPLOAD_MB", "50")

import streamlit

logger = logging.getLogger(__name__)

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSS_PATH = Path(__file__).parent / "flask_app" / "static" / "css" / "streamlit.css"
THEME_CSS_PATH = Path(__file__).parent / "flask_app" / "static" / "css" / "theme.css"
TAILWIND_CSS_PATH = Path(__file__).parent / "flask_app" / "static" / "css" / "tailwind-streamlit.css"
THEME_SCRIPT_PATH = Path(__file__).parent / "flask_app" / "static" / "js" / "theme.js"

GUEST_MAX_MB = int(os.environ.get("KONVERTER_GUEST_MAX_UPLOAD_MB", "10") or "10")
USER_MAX_MB = int(os.environ.get("KONVERTER_USER_MAX_UPLOAD_MB", "50") or "50")

SESSION_COOKIE_NAME = "konverter_session"
SESSION_IDLE_SECONDS = max(
    30, int(os.environ.get("KONVERTER_SESSION_IDLE_SECONDS", str(4 * 60 * 60)) or 4 * 60 * 60)
)
PREVIEW_CACHE_KEY = "konverter_preview_cache"
PREVIEW_CACHE_LIMIT = 8

if __name__ == "__main__":
    from flask_app.services import store
    from flask_app.services.concurrency import QueueTimeout, conversion_slot
    from flask_app.services.converter import (
        convert_csv_to_excel,
        convert_pdf_to_excel,
        preview_csv_file,
        preview_pdf_file,
    )
    from flask_app.services.eta import ProgressReporter, format_interval

    streamlit.set_page_config(
        page_title="Konverter PDF/CSV ke Excel",
        layout="centered",
        initial_sidebar_state="collapsed",
    )

    if TAILWIND_CSS_PATH.exists():
        streamlit.markdown(
            f"<style>{TAILWIND_CSS_PATH.read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True,
        )

    if THEME_CSS_PATH.exists():
        streamlit.markdown(
            f"<style>{THEME_CSS_PATH.read_text(encoding='utf-8')}</style>",
            unsafe_allow_html=True,
        )

    if CSS_PATH.exists():
        streamlit.markdown(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

    if THEME_SCRIPT_PATH.exists():
        streamlit.iframe(
            f"<script>{THEME_SCRIPT_PATH.read_text(encoding='utf-8')}</script>",
            height=1,
            alt="konverter-theme",
        )


def _escape_html(value) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def _fit_row(row, column_count: int):
    values = ["" if value is None else str(value) for value in (row or [])]
    return (values + [""] * column_count)[:column_count]


def _looks_like_header(row, data_rows) -> bool:
    filled = [value for value in row if value.strip()]
    if len(filled) < 2 or not data_rows:
        return False
    text_like = [value for value in filled if re.search(r"[A-Za-z\u00C0-\u00FF]", value)]
    same_as_data = any("|".join(data_row).lower() == "|".join(row).lower() for data_row in data_rows)
    return len(text_like) >= (len(filled) + 1) // 2 and not same_as_data


def _table_html(headers, rows) -> str:
    table_head_html = "".join(
        f"<th>{_escape_html(header) or f'Kolom {index + 1}'}</th>" for index, header in enumerate(headers)
    )
    table_body_html = "".join(
        "<tr>" + "".join(f"<td>{_escape_html(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    return (
        '<div class="max-h-[300px] w-full overflow-auto rounded-2xl border border-konverter-border bg-konverter-card">'
        '<table class="w-full border-collapse text-[0.82rem] '
        "[&_th]:whitespace-nowrap [&_th]:bg-konverter-th [&_th]:px-2.5 [&_th]:py-[9px] [&_th]:text-left "
        '[&_th]:font-extrabold [&_th]:text-konverter-heading [&_td]:whitespace-nowrap [&_td]:border-t '
        '[&_td]:border-konverter-border [&_td]:px-2.5 [&_td]:py-2 [&_td]:text-konverter-body">'
        f"<thead><tr>{table_head_html}</tr></thead><tbody>{table_body_html}</tbody></table></div>"
    )


def _copy_html(eyebrow: str, heading: str, description: str) -> str:
    return (
        "<div>"
        f'<p class="mb-[18px] w-fit rounded-full bg-konverter-blue-soft px-3.5 py-2 text-[0.78rem] '
        f'font-extrabold uppercase tracking-[0.08em] text-konverter-blue-ink">{eyebrow}</p>'
        f'<h1 class="m-0 max-w-[760px] p-0 text-[clamp(2.35rem,6vw,4.8rem)] font-bold leading-[0.96] '
        f'tracking-[-0.06em] text-konverter-ink">{heading}</h1>'
        f'<p class="mt-6 mb-0 max-w-[540px] text-[1.08rem] leading-[1.7] text-konverter-muted">{description}</p>'
        "</div>"
    )


def _hero_html(subtitle: str = "") -> str:
    description = subtitle or (
        "Unggah file, lihat pratinjau, lalu konversi dan download hasilnya sebagai file .xlsx."
    )
    return _copy_html(
        "Web &amp; Mobile Converter",
        "Ubah PDF atau CSV menjadi Excel dalam beberapa detik.",
        _escape_html(description),
    )


def _steps_html(active: int) -> str:
    labels = ["Pilih &amp; unggah", "Konversi", "Unduh"]
    items = []
    for index, label in enumerate(labels):
        is_active = index + 1 == active
        item_classes = (
            "bg-konverter-blue-soft text-konverter-blue-ink" if is_active else "bg-konverter-panel text-konverter-subtle"
        )
        badge_classes = "bg-konverter-blue text-white" if is_active else "bg-konverter-circle"
        items.append(
            f'<li class="flex items-center gap-2 rounded-2xl px-3 py-[10px] text-[0.82rem] '
            f'font-extrabold {item_classes}">'
            f'<span class="grid size-[22px] place-items-center rounded-full text-[0.78rem] {badge_classes}">'
            f"{index + 1}</span> {label}</li>"
        )
    joined = "".join(items)
    return f'<ol class="m-0 mb-1.5 grid list-none grid-cols-3 gap-2 p-0 max-[780px]:grid-cols-1" aria-label="Langkah">{joined}</ol>'


def _feature_html() -> str:
    article_classes = "rounded-3xl bg-konverter-navy p-[22px] text-white"
    return (
        '<section class="mt-5 grid grid-cols-3 gap-[18px] max-[780px]:grid-cols-1" aria-label="Fitur aplikasi">'
        f'<article class="{article_classes}"><span class="font-black text-konverter-skyline">01</span>'
        '<h2 class="mt-4 mb-2.5 text-[1.1rem] text-white">PDF ke Excel</h2>'
        '<p class="m-0 leading-[1.6] text-konverter-card-ink">Membaca tabel dari PDF dan menggabungkan hasilnya dalam satu sheet Excel.</p></article>'
        f'<article class="{article_classes}"><span class="font-black text-konverter-skyline">02</span>'
        '<h2 class="mt-4 mb-2.5 text-[1.1rem] text-white">CSV ke Excel</h2>'
        '<p class="m-0 leading-[1.6] text-konverter-card-ink">Mendeteksi pemisah koma atau titik koma secara otomatis.</p></article>'
        f'<article class="{article_classes}"><span class="font-black text-konverter-skyline">03</span>'
        '<h2 class="mt-4 mb-2.5 text-[1.1rem] text-white">Pratinjau Sebelum Konversi</h2>'
        '<p class="m-0 leading-[1.6] text-konverter-card-ink">Cek isi file (kolom, baris, jumlah halaman) sebelum proses konversi berjalan.</p></article>'
        "</section>"
    )


def _limit_banner_html(user, limit_mb: int) -> str:
    base_classes = (
        "my-1.5 rounded-2xl border px-4 py-3 text-[0.9rem] leading-[1.55] text-konverter-body"
    )
    if user:
        return (
            f'<div class="{base_classes} border-konverter-success-border bg-konverter-success-soft">'
            f'<strong>{_escape_html(user.get("email") or "Pengguna")}</strong> · '
            f"batas unggahan <strong>{limit_mb} MB</strong> · hasil otomatis tersimpan di riwayat."
            "</div>"
        )

    return (
        f'<div class="{base_classes} border-konverter-info-border bg-konverter-info-soft">'
        '<span class="rounded-full bg-konverter-navy px-2.5 py-[3px] text-[0.72rem] font-extrabold '
        'tracking-[0.03em] text-white">Mode tamu</span> '
        f"Batas unggahan <strong>{limit_mb} MB</strong> · file langsung dihapus setelah dikonversi. "
        f"Masuk untuk batas <strong>{USER_MAX_MB} MB</strong> dan riwayat konversi."
        "</div>"
    )


def _tool_card_html(icon: str, title: str, description: str) -> str:
    return (
        '<article class="konverter-tool-card rounded-[22px] p-5">'
        '<span class="inline-grid min-w-[52px] place-items-center rounded-xl bg-konverter-blue-soft '
        f'px-2.5 py-[7px] text-[0.76rem] font-black tracking-[0.04em] text-konverter-blue-ink">{icon}</span>'
        f'<h3 class="mt-3.5 mb-2 text-[1.05rem] text-konverter-ink">{title}</h3>'
        f'<p class="m-0 text-[0.9rem] leading-[1.6] text-konverter-muted">{description}</p>'
        "</article>"
    )


def _render_tool_cards() -> None:
    options = (
        (
            "konverter-tool-pdf",
            "konverter-pick-pdf",
            "PDF",
            "PDF ke Excel",
            "Gabungkan tabel dari seluruh halaman menjadi satu sheet Excel.",
            "pdf",
        ),
        (
            "konverter-tool-csv",
            "konverter-pick-csv",
            "CSV",
            "CSV ke Excel",
            "Deteksi pemisah otomatis, langsung jadi .xlsx yang rapi.",
            "csv",
        ),
    )
    columns = streamlit.columns(2)
    for column, (container_key, button_key, icon, title, description, page) in zip(columns, options):
        with column:
            with streamlit.container(key=container_key):
                streamlit.markdown(_tool_card_html(icon, title, description), unsafe_allow_html=True)
                if streamlit.button(title, key=button_key):
                    _navigate(page)


def _preview_html(kind: str, data: dict, filename: str, size_bytes: int) -> str:
    parts = [
        '<div class="mt-1.5 flex flex-wrap items-center gap-2">',
        f'<span class="rounded-full bg-konverter-navy px-3 py-1.5 text-[0.8rem] font-extrabold text-white '
        f'[overflow-wrap:anywhere]">{_escape_html(filename)}</span>',
    ]

    if kind == "csv-to-excel":
        rows = data["sample_rows"]
        parts.append(
            f'<span class="text-[0.84rem] font-bold text-konverter-muted">'
            f'{_format_bytes(size_bytes)} · {data["row_count"]} baris</span>'
        )
        parts.append("</div>")
        table = _table_html(data["headers"], rows)
        caption = f"Menampilkan {len(rows)} dari {data['row_count']} baris."
    else:
        raw_rows = data["sample_rows"]
        column_count = max(1, max((len(row) for row in raw_rows), default=0))
        normalized = [_fit_row(row, column_count) for row in raw_rows]
        first_row = normalized[0] if normalized else []
        has_header = _looks_like_header(first_row, normalized[1:])

        if has_header:
            headers = first_row
            rows = normalized[1:]
        else:
            headers = [f"Kolom {index + 1}" for index in range(column_count)]
            rows = normalized

        parts.append(
            f'<span class="text-[0.84rem] font-bold text-konverter-muted">'
            f'{_format_bytes(size_bytes)} · {data["page_count"]} halaman</span>'
        )
        parts.append("</div>")

        estimate = int(data.get("estimated_seconds") or 0)
        if estimate > 0:
            parts.append(
                f'<p class="mt-2.5 mb-0 text-[0.84rem] font-bold text-konverter-info-ink">'
                f"Perkiraan waktu konversi: ± {format_interval(estimate)}</p>"
            )

        table = _table_html(headers, rows)
        caption = (
            f"Menampilkan {len(raw_rows)} baris dari halaman pertama "
            f"(total {data['page_count']} halaman)."
        )

    parts.append(table)
    parts.append(f'<p class="m-0 text-[0.8rem] text-konverter-subtle">{_escape_html(caption)}</p>')
    return "".join(parts)


def _overlay_html(percent, message, eta, elapsed) -> str:
    percent = max(0, min(100, int(round(percent))))
    eta_line = ""
    running_line = ""

    if percent >= 100:
        eta_line = (
            f'<p class="mt-2.5 mb-1 text-[0.82rem] font-semibold text-konverter-faint">'
            f"Selesai dalam {format_interval(elapsed)}</p>"
        )
    elif eta is not None and eta >= 0:
        eta_line = (
            f'<p class="mt-2.5 mb-1 text-[0.82rem] font-semibold text-konverter-faint">'
            f"± {format_interval(eta)} tersisa</p>"
        )

    if percent < 100 and elapsed and elapsed > 0:
        running_line = (
            '<p class="mt-0 mb-5 text-[0.82rem] font-semibold text-konverter-faint">'
            f"berjalan {format_interval(elapsed)}</p>"
        )

    return (
        '<div class="fixed inset-0 z-[99999] grid place-items-center bg-konverter-overlay p-5">'
        '<div class="w-[min(430px,100%)] rounded-[26px] bg-konverter-card p-[26px] text-left shadow-[0_26px_60px_rgba(8,15,32,0.35)]" '
        'role="dialog" aria-modal="true">'
        '<h2 class="m-0 p-0 text-[1.24rem] tracking-[-0.02em] text-konverter-ink">Memproses file</h2>'
        f'<p class="mt-2.5 mb-5 text-[0.94rem] text-konverter-muted">{_escape_html(message or "Memproses file")}</p>'
        f"{eta_line}{running_line}"
        '<div class="h-3 overflow-hidden rounded-full bg-konverter-track">'
        '<div class="h-full rounded-[inherit] bg-gradient-to-r from-[#1b49d8] to-[#4b8bff] '
        f'transition-[width] duration-300 ease-out" style="width:{percent}%"></div>'
        "</div>"
        f'<p class="mt-3 mb-0 text-[0.9rem] font-extrabold text-konverter-emphasis">{percent}%</p>'
        "</div></div>"
    )


def _loading_html(title: str, text: str) -> str:
    return (
        '<div class="konverter-boot" role="status" aria-live="polite">'
        '<div class="konverter-boot-card">'
        '<span class="konverter-boot-spinner" aria-hidden="true"></span>'
        f'<p class="konverter-boot-title">{_escape_html(title)}</p>'
        f'<p class="konverter-boot-text">{_escape_html(text)}</p>'
        "</div></div>"
    )


def _boot_html() -> str:
    return _loading_html("Memulihkan sesi…", "Menyiapkan akun dan riwayat konversimu.")


def _session_idle_text() -> str:
    minutes = max(1, SESSION_IDLE_SECONDS // 60)

    if minutes >= 60 and minutes % 60 == 0:
        return f"{minutes // 60} jam"

    return f"{minutes} menit"


_SESSION_BRIDGE_TEMPLATE = """
(function () {
  "use strict";
  var cookieName = "__COOKIE_NAME__";
  var idleSeconds = __IDLE_SECONDS__;
  var idleLimitMs = idleSeconds * 1000;
  var payloadBase64 = "__PAYLOAD__";
  var shouldClear = __CLEAR__;
  var hostWindow = window;
  var hostDocument = document;

  try {
    if (window.parent && window.parent !== window) {
      hostWindow = window.parent;
      hostDocument = hostWindow.document;
    }
  } catch (error) {
    return;
  }

  function cookieSuffix(maxAge) {
    return "; path=/; max-age=" + maxAge + "; SameSite=Lax" +
      (hostWindow.location.protocol === "https:" ? "; Secure" : "");
  }

  function writeCookie(value, maxAge) {
    hostDocument.cookie = cookieName + "=" + value + cookieSuffix(maxAge);
  }

  function decodePayload(value) {
    var normalized = value.replace(/-/g, "+").replace(/_/g, "/");

    while (normalized.length % 4) {
      normalized += "=";
    }

    var binary = atob(normalized);
    var bytes = new Uint8Array(binary.length);

    for (var index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }

    return JSON.parse(new TextDecoder().decode(bytes));
  }

  function persistSession() {
    var payload = hostWindow.__konverterSessionPayload;

    if (!payload) {
      return;
    }

    payload.last_active = Math.floor(Date.now() / 1000);
    writeCookie(encodeURIComponent(JSON.stringify(payload)), idleSeconds);
  }

  function checkIdle() {
    if (!hostWindow.__konverterSessionPayload) {
      return;
    }

    var lastActive = hostWindow.__konverterSessionLastActive || Date.now();

    if (Date.now() - lastActive > idleLimitMs) {
      hostWindow.__konverterSessionPayload = null;
      writeCookie("", 0);
      hostWindow.location.reload();
    }
  }

  hostWindow.__konverterSessionWrite = persistSession;
  hostWindow.__konverterSessionLastActive = Date.now();

  if (shouldClear || !payloadBase64) {
    hostWindow.__konverterSessionPayload = null;
    writeCookie("", 0);
    return;
  }

  try {
    hostWindow.__konverterSessionPayload = decodePayload(payloadBase64);
  } catch (error) {
    hostWindow.__konverterSessionPayload = null;
    writeCookie("", 0);
    return;
  }

  persistSession();

  if (!hostDocument.__konverterSessionBound) {
    hostDocument.__konverterSessionBound = true;
    var lastWrite = 0;

    ["click", "keydown", "scroll", "touchstart", "visibilitychange"].forEach(function (eventName) {
      hostDocument.addEventListener(eventName, function () {
        checkIdle();

        var now = Date.now();
        hostWindow.__konverterSessionLastActive = now;

        if (now - lastWrite >= 30000) {
          lastWrite = now;

          if (hostWindow.__konverterSessionWrite) {
            hostWindow.__konverterSessionWrite();
          }
        }
      }, true);
    });

    hostWindow.setInterval(checkIdle, 30000);
  }
})();
"""


def _session_bridge_html(payload=None, clear: bool = False) -> str:
    encoded = ""

    if payload and not clear:
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii")

    script = (
        _SESSION_BRIDGE_TEMPLATE.replace("__COOKIE_NAME__", SESSION_COOKIE_NAME)
        .replace("__IDLE_SECONDS__", str(SESSION_IDLE_SECONDS))
        .replace("__PAYLOAD__", encoded)
        .replace("__CLEAR__", "true" if clear else "false")
    )
    return f"<script>{script}</script>"


def _render_session_bridge(payload=None, clear: bool = False) -> None:
    streamlit.iframe(_session_bridge_html(payload, clear), height=1, alt="konverter-session")


def _restore_cookie_session():
    session_state = streamlit.session_state

    try:
        raw_cookie = streamlit.context.cookies.get(SESSION_COOKIE_NAME)
    except Exception:
        raw_cookie = None

    if not raw_cookie:
        return None

    try:
        payload = json.loads(unquote(str(raw_cookie)))
        last_active = int(float(payload.get("last_active") or 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        session_state["konverter_clear_cookie"] = True
        return None

    if not isinstance(payload, dict) or last_active <= 0:
        session_state["konverter_clear_cookie"] = True
        return None

    if time.time() - last_active > SESSION_IDLE_SECONDS:
        session_state["konverter_clear_cookie"] = True
        session_state["konverter_auth_notice"] = (
            f"Sesi berakhir karena tidak aktif selama {_session_idle_text()}. Silakan masuk lagi."
        )
        session_state["konverter_page"] = "auth"
        return None

    try:
        user = store.restore_session(payload)
    except Exception:
        logger.exception("Gagal memulihkan sesi login dari cookie")
        user = None

    if not user:
        session_state["konverter_clear_cookie"] = True
        return None

    session_state["session_last_active"] = last_active
    return user


def _boot_user():
    session_state = streamlit.session_state

    if session_state.get("konverter_booted"):
        return store.current_user()

    splash = streamlit.empty()
    splash.markdown(_boot_html(), unsafe_allow_html=True)
    started = time.perf_counter()

    try:
        user = store.current_user()

        if not user:
            user = _restore_cookie_session()
    except Exception:
        logger.exception("Gagal memulihkan sesi saat aplikasi dimuat")
        user = None

    remaining = 0.45 - (time.perf_counter() - started)
    if remaining > 0:
        time.sleep(remaining)

    session_state["konverter_booted"] = True

    if user:
        session_state.setdefault("session_last_active", time.time())

    splash.empty()
    return user


def _session_is_idle() -> bool:
    last_active = streamlit.session_state.get("session_last_active")

    if not last_active:
        return False

    try:
        return time.time() - float(last_active) > SESSION_IDLE_SECONDS
    except (TypeError, ValueError):
        return False


def _end_session(notice: str) -> None:
    try:
        store.sign_out()
    except Exception:
        logger.exception("Gagal mengakhiri sesi login")

    session_state = streamlit.session_state
    session_state["konverter_clear_cookie"] = True
    session_state["konverter_auth_notice"] = notice
    session_state["konverter_page"] = "auth"
    session_state["session_last_active"] = None
    streamlit.rerun()


def _current_session_payload():
    try:
        payload = store.export_session()
    except Exception:
        logger.exception("Gagal menyiapkan data sesi untuk cookie")
        return None

    if not payload:
        return None

    payload["v"] = max(int(payload.get("v") or 1), 1)
    payload["last_active"] = int(streamlit.session_state.get("session_last_active") or time.time())
    return payload


def _apply_session_bridge(user) -> None:
    session_state = streamlit.session_state

    if session_state.pop("konverter_clear_cookie", False):
        _render_session_bridge(clear=True)
        return

    if not user:
        return

    payload = _current_session_payload()

    if payload:
        _render_session_bridge(payload)


def _status_label(status: str) -> str:
    return {
        "completed": "Selesai",
        "processing": "Diproses",
        "failed": "Gagal",
    }.get(status, status or "-")


def _signature(uploaded) -> str:
    return f"{uploaded.name}:{uploaded.size}:{uploaded.type}"


def _save_upload(uploaded) -> str:
    session_state = streamlit.session_state
    signature = _signature(uploaded)

    if session_state.get("upload_sig") == signature and session_state.get("input_path"):
        return session_state["input_path"]

    old_path = session_state.get("input_path")
    if old_path:
        try:
            os.unlink(old_path)
        except OSError:
            pass

    suffix = Path(uploaded.name).suffix.lower() or ".bin"
    file_descriptor, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(file_descriptor, "wb") as handle:
        handle.write(uploaded.getvalue())

    session_state["input_path"] = path
    session_state["upload_sig"] = signature
    session_state.pop(PREVIEW_CACHE_KEY, None)
    session_state.pop("convert_error", None)
    for key in ("output_bytes", "output_name", "output_sig", "output_kind"):
        session_state.pop(key, None)
    return path


def _drop_temp_input() -> None:
    session_state = streamlit.session_state
    path = session_state.pop("input_path", None)
    session_state.pop("upload_sig", None)

    if path:
        session_state.pop(PREVIEW_CACHE_KEY, None)

        try:
            os.unlink(path)
        except OSError:
            pass


def _cached_preview(kind: str, path: str, size: int, modification_time_nanoseconds: int):
    session_state = streamlit.session_state
    cache = session_state.get(PREVIEW_CACHE_KEY)

    if not isinstance(cache, dict):
        cache = {}
        session_state[PREVIEW_CACHE_KEY] = cache

    cache_key = (kind, path, size, modification_time_nanoseconds)

    if cache_key in cache:
        return cache[cache_key]

    with streamlit.spinner("Memeriksa file..."):
        if kind == "csv-to-excel":
            data = preview_csv_file(path)
        else:
            data = preview_pdf_file(path)

    cache[cache_key] = data

    while len(cache) > PREVIEW_CACHE_LIMIT:
        cache.pop(next(iter(cache)))

    return data


def _navigate(page: str) -> None:
    streamlit.session_state["konverter_page"] = page
    streamlit.rerun()


def _brand_html() -> str:
    return (
        '<div class="konverter-brand">'
        '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" '
        'stroke-width="1.5" stroke="currentColor" class="konverter-brand-icon">'
        '<path stroke-linecap="round" stroke-linejoin="round" '
        'd="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m.75 12 3 3m0 0 3-3m-3 3v-6m-1.5-9H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"/>'
        '</svg>'
        '<span class="konverter-brand-text">'
        '<span class="konverter-brand-name">Konverter</span>'
        '<span class="konverter-brand-sub">PDF &amp; CSV → Excel</span>'
        '</span></div>'
    )


def _theme_toggle_html() -> str:
    return (
        '<button type="button" data-konverter-theme-toggle class="konverter-theme-toggle" '
        'aria-label="Aktifkan tema gelap" aria-pressed="false" title="Ganti tema terang atau gelap">'
        '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" '
        'stroke="currentColor" class="konverter-theme-icon konverter-theme-icon-moon" aria-hidden="true">'
        '<path stroke-linecap="round" stroke-linejoin="round" '
        'd="M21.752 15.002A9.72 9.72 0 0 1 18 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 0 0 3 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 0 0 9.002-5.998Z"/>'
        "</svg>"
        '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" '
        'stroke="currentColor" class="konverter-theme-icon konverter-theme-icon-sun" aria-hidden="true">'
        '<path stroke-linecap="round" stroke-linejoin="round" '
        'd="M12 3v2.25m6.364.386-1.591 1.591M21 12h-2.25m-.386 6.364-1.591-1.591M12 18.75V21m-4.773-4.227-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0Z"/>'
        "</svg>"
        "</button>"
    )


def _brand_row(user, back_label=None, show_login=True, show_history=True) -> None:
    configured = store.is_configured()
    slots = []

    if back_label:
        slots.append("back")
    if user and show_history:
        slots.append("history")

    if configured and user:
        slots.append("keluar")
    elif configured and not user and show_login:
        slots.append("masuk")
    elif not configured:
        slots.append("note")

    slots.append("theme")

    with streamlit.container(key="konverter-navbar"):
        brand_col, *action_cols = streamlit.columns(
            [4] + [1] * len(slots), vertical_alignment="center", gap="small"
        )

        with brand_col:
            streamlit.markdown(_brand_html(), unsafe_allow_html=True)

        for slot, col in zip(slots, action_cols):
            with col:
                if slot == "back":
                    if streamlit.button(back_label, key="konverter-navbar-back", width="stretch"):
                        _navigate("home")
                elif slot == "history":
                    if streamlit.button("Riwayat", key="konverter-navbar-history", width="stretch"):
                        _navigate("history")
                elif slot == "masuk":
                    if streamlit.button("Masuk", key="konverter-navbar-sign-in", type="primary",
                                 width="stretch"):
                        _navigate("auth")
                elif slot == "keluar":
                    if streamlit.button("Keluar", key="konverter-navbar-sign-out", width="stretch"):
                        store.sign_out()
                        streamlit.session_state["konverter_clear_cookie"] = True
                        streamlit.session_state["session_last_active"] = None
                        _navigate("home")
                elif slot == "theme":
                    streamlit.markdown(_theme_toggle_html(), unsafe_allow_html=True)
                else:
                    streamlit.markdown(
                        '<span class="konverter-navbar-badge">Login belum aktif</span>',
                        unsafe_allow_html=True,
                    )


def _set_convert_error(kind: str, message: str, level: str = "error") -> None:
    streamlit.session_state["convert_error"] = {
        "kind": kind,
        "message": str(message),
        "level": level,
        "sig": streamlit.session_state.get("upload_sig"),
    }


def _clear_stale_convert_error() -> None:
    session_state = streamlit.session_state
    error = session_state.get("convert_error")

    if not isinstance(error, dict):
        return

    signature = error.get("sig")

    if not signature:
        return

    uploaded = session_state.get("konverter-upload")

    if uploaded is None or _signature(uploaded) != signature:
        session_state.pop("convert_error", None)


def _render_convert_error(kind: str) -> None:
    error = streamlit.session_state.get("convert_error")

    if not isinstance(error, dict) or error.get("kind") != kind:
        return

    with streamlit.container(key="konverter-convert-error"):
        if error.get("level") == "warning":
            streamlit.warning(error.get("message") or "Konversi belum selesai.")
        else:
            streamlit.error(error.get("message") or "Konversi gagal.")

        if streamlit.button("Tutup pesan", key="konverter-convert-error-dismiss"):
            streamlit.session_state.pop("convert_error", None)
            streamlit.rerun()


@streamlit.dialog("Konversi belum selesai", dismissible=True, on_dismiss="rerun")
def _convert_error_dialog(message: str, level: str = "error") -> None:
    if level == "warning":
        streamlit.warning(message)
    else:
        streamlit.error(message)

    streamlit.caption("File sumber tetap aman. Periksa pesan di atas, lalu klik Konversi Sekarang lagi.")

    if streamlit.button("Tutup", key="konverter-dialog-close", type="primary", width="stretch"):
        streamlit.rerun()


def _finish_job_safe(job_id, status: str, **fields) -> None:
    if not job_id:
        return

    try:
        store.finish_job(job_id, status, **fields)
    except Exception:
        logger.exception("Gagal memperbarui status job %s menjadi %s", job_id, status)


def _page_home(user, limit_mb: int) -> None:
    _brand_row(user)

    with streamlit.container(key="konverter-hero"):
        streamlit.markdown(_hero_html(), unsafe_allow_html=True)
        streamlit.markdown(_limit_banner_html(user, limit_mb), unsafe_allow_html=True)
        _render_tool_cards()

    streamlit.markdown(_feature_html(), unsafe_allow_html=True)


def _page_tool(kind: str, user, limit_mb: int) -> None:
    title = "PDF ke Excel" if kind == "pdf-to-excel" else "CSV ke Excel"
    subtitle = (
        "Unggah PDF, cek pratinjau tabelnya, lalu unduh hasil Excel."
        if kind == "pdf-to-excel"
        else "Unggah CSV, cek pratinjau barisnya, lalu unduh hasil Excel."
    )
    active_step = 3 if streamlit.session_state.get("output_kind") == kind and streamlit.session_state.get(
        "output_sig"
    ) == streamlit.session_state.get("upload_sig") else (2 if streamlit.session_state.get("converting") else 1)

    _brand_row(user, back_label="← Kembali")

    _clear_stale_convert_error()

    if streamlit.session_state.pop("convert_pending", False) and not streamlit.session_state.get("converting"):
        streamlit.warning("Konversi sebelumnya terputus sebelum selesai. Silakan coba lagi.")

    _render_convert_error(kind)

    with streamlit.container(key="konverter-hero"):
        streamlit.markdown(_hero_html(subtitle), unsafe_allow_html=True)
        streamlit.markdown(_limit_banner_html(user, limit_mb), unsafe_allow_html=True)

        with streamlit.container(key="konverter-panel"):
            streamlit.markdown(f'<p class="mb-3 mt-0 p-0 text-[1.15rem] font-black text-konverter-ink">{_escape_html(title)}</p>', unsafe_allow_html=True)
            streamlit.markdown(_steps_html(active_step), unsafe_allow_html=True)

            uploaded = streamlit.file_uploader("File sumber", type=["pdf", "csv"], key="konverter-upload")
            preview_data = None

            if uploaded is None:
                _drop_temp_input()

            if uploaded is not None:
                expected = "pdf" if kind == "pdf-to-excel" else "csv"
                extension = Path(uploaded.name).suffix.lower().lstrip(".")
                size_mb = uploaded.size / (1024 * 1024)

                if extension != expected:
                    streamlit.warning(f"Untuk {title}, unggah file .{expected} (file saat ini .{extension}).")
                elif size_mb > limit_mb:
                    streamlit.warning(
                        f"Ukuran file {_format_bytes(uploaded.size)} melebihi batas {limit_mb} MB. "
                        + (
                            "Coba file yang lebih kecil."
                            if user
                            else f"Masuk untuk batas {USER_MAX_MB} MB."
                        )
                    )
                else:
                    input_path = _save_upload(uploaded)
                    file_stat = Path(input_path).stat()

                    try:
                        preview_data = _cached_preview(kind, input_path, file_stat.st_size, file_stat.st_mtime_ns)
                    except ValueError as error:
                        logger.warning("Pratinjau ditolak untuk %s: %s", input_path, error)
                        streamlit.warning(str(error))
                    except Exception as error:
                        logger.exception("Gagal membaca pratinjau file %s", input_path)
                        streamlit.error(f"Tidak dapat membaca file: {error}")
                    else:
                        streamlit.markdown(
                            _preview_html(kind, preview_data, uploaded.name, file_stat.st_size),
                            unsafe_allow_html=True,
                        )
                        _run_conversion(kind, user, uploaded, input_path, preview_data)

            output_ready = (
                streamlit.session_state.get("output_bytes")
                and streamlit.session_state.get("output_sig") == streamlit.session_state.get("upload_sig")
                and streamlit.session_state.get("output_kind") == kind
            )

            if output_ready and uploaded is not None:
                streamlit.download_button(
                    "Unduh Excel",
                    data=streamlit.session_state["output_bytes"],
                    file_name=streamlit.session_state.get("output_name", "hasil.xlsx"),
                    mime=EXCEL_MIME,
                    type="primary",
                    key="konverter-download",
                )
                if user and streamlit.session_state.get("output_job"):
                    streamlit.caption("Hasil ini tersimpan di riwayat konversi akunmu.")


def _run_conversion(kind: str, user, uploaded, input_path: str, preview_data: dict) -> None:
    if not streamlit.button("Konversi Sekarang", type="primary", key="konverter-convert"):
        return

    session_state = streamlit.session_state
    session_state.pop("convert_error", None)
    session_state["convert_pending"] = True
    overlay = streamlit.empty()

    state_lock = threading.Lock()
    state = {
        "percent": 0,
        "message": "Menyiapkan file",
        "eta": None,
        "eta_at": None,
        "done": False,
    }
    started = time.perf_counter()

    def on_update(percent, message, eta, _elapsed):
        with state_lock:
            now = time.perf_counter()

            if eta is not None:
                # ETA dari server dipakai langsung, termasuk saat nilainya naik.
                state["eta"] = eta
                state["eta_at"] = now
            elif state["eta"] is not None and state["eta_at"] is not None:
                # Server belum punya estimasi baru: biarkan hitungan mundur jalan.
                state["eta"] = max(state["eta"] - (now - state["eta_at"]), 0)
                state["eta_at"] = now

            state["percent"] = percent
            state["message"] = message

    def on_wait(_waited, position):
        with state_lock:
            state["message"] = f"Menunggu antrean (posisi {position})"

    def render():
        with state_lock:
            percent = state["percent"]
            message = state["message"]
            eta = state["eta"]
            eta_at = state["eta_at"]
            done = state["done"]

        now = time.perf_counter()
        elapsed = now - started
        display_eta = eta

        if eta is not None and eta_at is not None:
            display_eta = max(0, eta - (now - eta_at))

        overlay.markdown(
            _overlay_html(percent, message, display_eta, elapsed),
            unsafe_allow_html=True,
        )
        return done

    render()

    reporter = ProgressReporter(on_update)
    page_count = preview_data.get("page_count") if kind == "pdf-to-excel" else None
    output_name = f"{Path(uploaded.name).stem}.xlsx"

    file_descriptor, output_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(file_descriptor)

    streamlit.session_state["converting"] = True
    object_id = uuid4().hex
    job_id = None
    result_name = None
    duration_started = time.perf_counter()
    worker_error = None

    if user:
        try:
            source_path = store.object_path(user["id"], object_id, uploaded.name)
            store.upload_file(
                store.SOURCE_BUCKET,
                source_path,
                uploaded.getvalue(),
                uploaded.type or "application/octet-stream",
            )
            job_id = store.create_job(
                user["id"], kind, uploaded.name, uploaded.size, source_path
            )
        except Exception as error:
            logger.exception("Gagal menyimpan sumber ke riwayat (job=%s)", job_id)
            streamlit.warning(f"Gagal menyimpan sumber ke riwayat: {error}")

    def conversion_worker():
        nonlocal worker_error
        try:
            with conversion_slot(on_wait=on_wait):
                if kind == "csv-to-excel":
                    convert_csv_to_excel(input_path, output_path, reporter)
                else:
                    convert_pdf_to_excel(
                        input_path,
                        output_path,
                        reporter,
                        total_pages=page_count,
                        strategy=preview_data.get("strategy"),
                    )
        except BaseException as error:
            worker_error = error
        finally:
            with state_lock:
                state["done"] = True

    worker = threading.Thread(target=conversion_worker, name="konverter-conversion", daemon=True)
    worker.start()

    error_message = None
    error_level = "error"

    try:
        while not render():
            time.sleep(0.4)

        if worker_error is not None:
            raise worker_error

        result_bytes = Path(output_path).read_bytes()
        streamlit.session_state["output_bytes"] = result_bytes
        streamlit.session_state["output_name"] = output_name
        streamlit.session_state["output_sig"] = streamlit.session_state.get("upload_sig")
        streamlit.session_state["output_kind"] = kind
        streamlit.session_state["output_job"] = job_id

        if user and job_id:
            result_name = f"{Path(output_name).stem}-{object_id[:8]}.xlsx"
            try:
                result_path = store.object_path(user["id"], object_id, result_name)
                store.upload_file(store.RESULT_BUCKET, result_path, result_bytes, EXCEL_MIME)
                _finish_job_safe(
                    job_id,
                    "completed",
                    result_name=result_name,
                    result_path=result_path,
                    output_size_bytes=len(result_bytes),
                    page_count=page_count,
                    row_count=preview_data.get("row_count") if kind == "csv-to-excel" else None,
                    duration_ms=int((time.perf_counter() - duration_started) * 1000),
                )
            except Exception as error:
                logger.exception("Gagal menyimpan hasil ke riwayat (job=%s)", job_id)
                streamlit.warning(f"Hasil tidak tersimpan ke riwayat: {error}")
    except ValueError as error:
        logger.warning("Konversi ditolak (job=%s): %s", job_id, error)
        _finish_job_safe(job_id, "failed", error=str(error))
        error_message = f"Konversi gagal: {error}"
    except QueueTimeout as error:
        logger.warning("Konversi menunggu antrean terlalu lama (job=%s): %s", job_id, error)
        _finish_job_safe(job_id, "failed", error=str(error))
        error_message = str(error)
        error_level = "warning"
    except Exception as error:
        logger.exception("Konversi gagal (kind=%s, job=%s)", kind, job_id)
        _finish_job_safe(job_id, "failed", error=str(error))
        error_message = f"Konversi gagal: {error}"
    finally:
        worker.join(timeout=5)
        overlay.empty()
        streamlit.session_state["converting"] = False
        streamlit.session_state["convert_pending"] = False
        try:
            os.unlink(output_path)
        except OSError:
            pass

    if error_message:
        _set_convert_error(kind, error_message, error_level)
        _convert_error_dialog(error_message, error_level)


def _page_auth() -> None:
    _brand_row(None, back_label="← Kembali", show_login=False)

    with streamlit.container(key="konverter-hero"):
        streamlit.markdown(
            _copy_html(
                "Akun",
                "Masuk untuk menyimpan riwayat.",
                f"Login membuka batas unggahan {USER_MAX_MB} MB dan menyimpan hasil konversi di riwayat akunmu.",
            ),
            unsafe_allow_html=True,
        )

        with streamlit.container(key="konverter-panel"):
            notice = streamlit.session_state.pop("konverter_auth_notice", None)

            if notice:
                streamlit.warning(notice)

            tab_in, tab_up = streamlit.tabs(["Masuk", "Daftar"])

            with tab_in:
                email = streamlit.text_input("Email", key="konverter-sign-in-email")
                password = streamlit.text_input("Kata sandi", type="password", key="konverter-sign-in-password")

                with streamlit.container(horizontal_alignment="right"):
                    submitted = streamlit.button(
                        "Masuk", type="primary", key="konverter-sign-in-submit", width="content"
                    )

                if submitted:
                    if not email or not password:
                        streamlit.warning("Isi email dan kata sandi.")
                    else:
                        loading = streamlit.empty()
                        loading.markdown(
                            _loading_html("Memeriksa akun...", "Menghubungkan ke server akun."),
                            unsafe_allow_html=True,
                        )

                        try:
                            store.sign_in(email.strip(), password)
                        except Exception as error:
                            logger.exception("Login gagal")
                            loading.empty()
                            streamlit.error(_auth_error(error))
                        else:
                            streamlit.session_state["session_last_active"] = time.time()
                            streamlit.session_state.pop("konverter_auth_notice", None)
                            loading.empty()
                            _navigate("home")

            with tab_up:
                email = streamlit.text_input("Email baru", key="konverter-sign-up-email")
                password = streamlit.text_input("Kata sandi baru (min. 8 karakter)", type="password", key="konverter-sign-up-password")

                with streamlit.container(horizontal_alignment="right"):
                    submitted = streamlit.button(
                        "Daftar", type="primary", key="konverter-sign-up-submit", width="content"
                    )

                if submitted:
                    if not email or len(password) < 8:
                        streamlit.warning("Isi email dan kata sandi minimal 8 karakter.")
                    else:
                        loading = streamlit.empty()
                        loading.markdown(
                            _loading_html("Membuat akun...", "Menyiapkan akun Konverter untukmu."),
                            unsafe_allow_html=True,
                        )

                        try:
                            result = store.sign_up(email.strip(), password)
                        except Exception as error:
                            logger.exception("Pendaftaran akun gagal")
                            loading.empty()
                            streamlit.error(_auth_error(error))
                        else:
                            loading.empty()

                            if result.get("needs_confirmation"):
                                streamlit.success(
                                    "Akun dibuat. Cek email untuk konfirmasi, lalu masuk di tab Masuk."
                                )
                            else:
                                streamlit.session_state["session_last_active"] = time.time()
                                streamlit.session_state.pop("konverter_auth_notice", None)
                                _navigate("home")


def _auth_error(error: Exception) -> str:
    message = getattr(error, "message", None) or str(error)
    error_type = str(getattr(error, "type", "") or "")
    lower_text = message.lower()

    if (
        "name or service not known" in lower_text
        or "getaddrinfo" in lower_text
        or "errno -2" in lower_text
        or "name resolution" in lower_text
        or "nodename nor servname" in lower_text
        or "temporary failure in name resolution" in lower_text
    ):
        return (
            "Server akun tidak dapat dihubungi (DNS gagal). Periksa koneksi atau konfigurasi "
            "backend di Secrets, lalu coba lagi. Kamu tetap bisa konversi sebagai tamu."
        )
    if "invalid login credentials" in lower_text or "user_invalid_credentials" in error_type:
        return "Email atau kata sandi salah."
    if "already registered" in lower_text or "already exists" in lower_text or "user_already_exists" in error_type:
        return "Email ini sudah terdaftar. Coba masuk."
    if "email not confirmed" in lower_text or "user_email_not_verified" in error_type:
        return "Email belum dikonfirmasi. Cek kotak masuk untuk tautan verifikasi."
    if "user_session_limit" in error_type or "session limit" in lower_text or "maximum number of sessions" in lower_text:
        return "Terlalu banyak sesi aktif. Keluar dari perangkat lain lalu coba lagi."
    if "password" in lower_text and ("short" in lower_text or "weak" in lower_text or "must be" in lower_text or "length" in lower_text):
        return "Kata sandi terlalu lemah. Gunakan minimal 8 karakter."
    return f"Gagal: {message}"


def _page_history(user) -> None:
    _brand_row(user, back_label="← Kembali ke alat", show_history=False)

    with streamlit.container(key="konverter-hero"):
        streamlit.markdown(
            _copy_html(
                "Riwayat",
                "Konversi yang kamu simpan.",
                "Hasil dapat diunduh ulang kapan saja selama masih tersimpan.",
            ),
            unsafe_allow_html=True,
        )

        with streamlit.container(key="konverter-panel"):
            jobs = store.list_jobs(user["id"])

            if not jobs:
                streamlit.info("Belum ada riwayat konversi. Mulai dari halaman Alat.")
            for job in jobs:
                _render_job(job, user)


def _render_job(job: dict, user) -> None:
    status = job.get("status") or "completed"
    created = (job.get("created_at") or "")[:19].replace("T", " ")
    facts = [created, _format_bytes(job.get("source_size_bytes") or 0)]
    if job.get("page_count"):
        facts.append(f"{job['page_count']} halaman")
    if job.get("row_count"):
        facts.append(f"{job['row_count']} baris")

    status_classes = {
        "completed": "bg-konverter-success-soft text-konverter-success",
        "processing": "bg-konverter-warn-soft text-konverter-warn",
        "failed": "bg-konverter-danger-soft text-konverter-danger",
    }.get(status, "bg-konverter-th text-konverter-muted")

    streamlit.markdown(
        '<div class="flex flex-wrap items-center gap-2.5 py-3">'
        f'<span class="rounded-full px-2.5 py-[5px] text-[0.72rem] font-black tracking-[0.03em] '
        f'{status_classes}">{_escape_html(_status_label(status))}</span>'
        f'<span class="font-extrabold text-konverter-ink [overflow-wrap:anywhere]">'
        f'{_escape_html(job.get("original_name") or "-")}</span>'
        f'<span class="text-[0.84rem] font-bold text-konverter-muted">'
        f'{" · ".join(_escape_html(item) for item in facts)}</span>'
        "</div>",
        unsafe_allow_html=True,
    )

    if status != "completed":
        if job.get("error"):
            streamlit.caption(f"Alasan: {job['error']}")
        return

    actions = streamlit.columns([1, 1, 2])
    with actions[0]:
        _render_download(job)
    with actions[1]:
        with streamlit.popover("Hapus"):
            streamlit.write("Hapus konversi ini beserta filenya?")
            if streamlit.button("Ya, hapus", key=f"konverter-delete-confirm-{job['id']}"):
                streamlit.session_state.pop(f"konverter-download-data-{job['id']}", None)
                store.delete_job(user["id"], job)
                streamlit.rerun()


def _render_download(job: dict) -> None:
    if not job.get("result_path"):
        streamlit.caption("Hasil tidak tersedia")
        return

    download_url = store.signed_url(store.RESULT_BUCKET, job["result_path"])

    if download_url:
        streamlit.link_button("Unduh", download_url)
        return

    cache_key = f"konverter-download-data-{job['id']}"
    data = streamlit.session_state.get(cache_key)

    if not data:
        if streamlit.button("Siapkan unduhan", key=f"konverter-download-prepare-{job['id']}"):
            with streamlit.spinner("Menyiapkan file..."):
                data = store.download_bytes(store.RESULT_BUCKET, job["result_path"])

            if data:
                streamlit.session_state[cache_key] = data
                streamlit.rerun()
            else:
                streamlit.caption("Hasil tidak dapat diambil")
        return

    streamlit.download_button(
        "Unduh",
        data=data,
        file_name=job.get("result_name") or "hasil.xlsx",
        mime=EXCEL_MIME,
        key=f"konverter-download-button-{job['id']}",
    )


if __name__ == "__main__":
    user = _boot_user()

    if user:
        if _session_is_idle():
            _end_session(
                f"Sesi berakhir karena tidak aktif selama {_session_idle_text()}. Silakan masuk lagi."
            )

        try:
            user = store.ensure_session()
        except Exception:
            logger.exception("Gagal memperbarui sesi login")
            user = store.current_user()

        if user is None:
            _end_session("Sesi login sudah berakhir. Silakan masuk lagi.")

        streamlit.session_state["session_last_active"] = time.time()

    _apply_session_bridge(user)

    if user is None and streamlit.session_state.get("konverter_page") == "history":
        streamlit.session_state["konverter_page"] = "auth"

    page = streamlit.session_state.setdefault("konverter_page", "home")
    limit_mb = USER_MAX_MB if user else GUEST_MAX_MB

    if page in ("pdf", "csv"):
        _page_tool("pdf-to-excel" if page == "pdf" else "csv-to-excel", user, limit_mb)
    elif page == "history":
        _page_history(user)
    elif page == "auth":
        if not store.is_configured():
            streamlit.info(
                "Login belum dikonfigurasi. Isi blok `[supabase]` atau `[appwrite]` di "
                "`.streamlit/secrets.toml` untuk mengaktifkannya."
            )
        else:
            _page_auth()
    else:
        _page_home(user, limit_mb)
