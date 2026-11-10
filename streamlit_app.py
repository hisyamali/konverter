import html
import os
import re
import tempfile
import time
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("KONVERTER_MAX_WORKERS", "1" if os.name == "nt" else "2")
os.environ.setdefault("KONVERTER_MAX_UPLOAD_MB", "50")

import streamlit

from flask_app.services import store
from flask_app.services.concurrency import QueueTimeout, conversion_slot
from flask_app.services.converter import (
    convert_csv_to_excel,
    convert_pdf_to_excel,
    preview_csv_file,
    preview_pdf_file,
)
from flask_app.services.eta import ProgressReporter, format_interval

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSS_PATH = Path(__file__).parent / "flask_app" / "static" / "css" / "streamlit.css"
TAILWIND_CSS_PATH = Path(__file__).parent / "flask_app" / "static" / "css" / "tailwind-streamlit.css"

GUEST_MAX_MB = int(os.environ.get("KONVERTER_GUEST_MAX_UPLOAD_MB", "10") or "10")
USER_MAX_MB = int(os.environ.get("KONVERTER_USER_MAX_UPLOAD_MB", "50") or "50")

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

if CSS_PATH.exists():
    streamlit.markdown(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


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
        '<div class="max-h-[300px] w-full overflow-auto rounded-2xl border border-kv-border bg-white">'
        '<table class="w-full border-collapse text-[0.82rem] '
        "[&_th]:whitespace-nowrap [&_th]:bg-kv-th [&_th]:px-2.5 [&_th]:py-[9px] [&_th]:text-left "
        '[&_th]:font-extrabold [&_th]:text-kv-heading [&_td]:whitespace-nowrap [&_td]:border-t '
        '[&_td]:border-[#eef2f7] [&_td]:px-2.5 [&_td]:py-2 [&_td]:text-kv-body">'
        f"<thead><tr>{table_head_html}</tr></thead><tbody>{table_body_html}</tbody></table></div>"
    )


def _copy_html(eyebrow: str, heading: str, description: str) -> str:
    return (
        "<div>"
        f'<p class="mb-[18px] w-fit rounded-full bg-kv-blue-soft px-3.5 py-2 text-[0.78rem] '
        f'font-extrabold uppercase tracking-[0.08em] text-kv-blue-ink">{eyebrow}</p>'
        f'<h1 class="m-0 max-w-[760px] p-0 text-[clamp(2.35rem,6vw,4.8rem)] font-bold leading-[0.96] '
        f'tracking-[-0.06em] text-kv-ink">{heading}</h1>'
        f'<p class="mt-6 mb-0 max-w-[540px] text-[1.08rem] leading-[1.7] text-kv-muted">{description}</p>'
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
            "bg-kv-blue-soft text-kv-blue-ink" if is_active else "bg-kv-panel text-kv-subtle"
        )
        badge_classes = "bg-kv-blue text-white" if is_active else "bg-kv-circle"
        items.append(
            f'<li class="flex items-center gap-2 rounded-2xl px-3 py-[10px] text-[0.82rem] '
            f'font-extrabold {item_classes}">'
            f'<span class="grid size-[22px] place-items-center rounded-full text-[0.78rem] {badge_classes}">'
            f"{index + 1}</span> {label}</li>"
        )
    joined = "".join(items)
    return f'<ol class="m-0 mb-1.5 grid list-none grid-cols-3 gap-2 p-0 max-[780px]:grid-cols-1" aria-label="Langkah">{joined}</ol>'


def _feature_html() -> str:
    article_classes = "rounded-3xl bg-kv-navy p-[22px] text-white"
    return (
        '<section class="mt-5 grid grid-cols-3 gap-[18px] max-[780px]:grid-cols-1" aria-label="Fitur aplikasi">'
        f'<article class="{article_classes}"><span class="font-black text-kv-skyline">01</span>'
        '<h2 class="mt-4 mb-2.5 text-[1.1rem] text-white">PDF ke Excel</h2>'
        '<p class="m-0 leading-[1.6] text-kv-card-ink">Membaca tabel dari PDF dan menggabungkan hasilnya dalam satu sheet Excel.</p></article>'
        f'<article class="{article_classes}"><span class="font-black text-kv-skyline">02</span>'
        '<h2 class="mt-4 mb-2.5 text-[1.1rem] text-white">CSV ke Excel</h2>'
        '<p class="m-0 leading-[1.6] text-kv-card-ink">Mendeteksi pemisah koma atau titik koma secara otomatis.</p></article>'
        f'<article class="{article_classes}"><span class="font-black text-kv-skyline">03</span>'
        '<h2 class="mt-4 mb-2.5 text-[1.1rem] text-white">Pratinjau Sebelum Konversi</h2>'
        '<p class="m-0 leading-[1.6] text-kv-card-ink">Cek isi file (kolom, baris, jumlah halaman) sebelum proses konversi berjalan.</p></article>'
        "</section>"
    )


def _limit_banner_html(user, limit_mb: int) -> str:
    base_classes = (
        "my-1.5 rounded-2xl border px-4 py-3 text-[0.9rem] leading-[1.55] text-kv-body"
    )
    if user:
        return (
            f'<div class="{base_classes} border-[#bfe3c8] bg-[#f1fbf4]">'
            f'<strong>{_escape_html(user.get("email") or "Pengguna")}</strong> · '
            f"batas unggahan <strong>{limit_mb} MB</strong> · hasil otomatis tersimpan di riwayat."
            "</div>"
        )

    return (
        f'<div class="{base_classes} border-kv-blue-soft bg-[#f2f6ff]">'
        '<span class="rounded-full bg-kv-navy px-2.5 py-[3px] text-[0.72rem] font-extrabold '
        'tracking-[0.03em] text-white">Mode tamu</span> '
        f"Batas unggahan <strong>{limit_mb} MB</strong> · file langsung dihapus setelah dikonversi. "
        f"Masuk untuk batas <strong>{USER_MAX_MB} MB</strong> dan riwayat konversi."
        "</div>"
    )


def _tool_card_html(icon: str, title: str, description: str) -> str:
    return (
        '<article class="kv-tool-card rounded-[22px] border border-kv-border bg-white p-5 '
        'shadow-[0_12px_30px_rgba(55,76,118,0.08)]">'
        '<span class="inline-grid min-w-[52px] place-items-center rounded-xl bg-kv-blue-soft '
        f'px-2.5 py-[7px] text-[0.76rem] font-black tracking-[0.04em] text-kv-blue-ink">{icon}</span>'
        f'<h3 class="mt-3.5 mb-2 text-[1.05rem] text-kv-ink">{title}</h3>'
        f'<p class="m-0 text-[0.9rem] leading-[1.6] text-kv-muted">{description}</p>'
        "</article>"
    )


def _render_tool_cards() -> None:
    options = (
        (
            "kv-tool-pdf",
            "kv-pick-pdf",
            "PDF",
            "PDF ke Excel",
            "Gabungkan tabel dari seluruh halaman menjadi satu sheet Excel.",
            "pdf",
        ),
        (
            "kv-tool-csv",
            "kv-pick-csv",
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
        f'<span class="rounded-full bg-kv-navy px-3 py-1.5 text-[0.8rem] font-extrabold text-white '
        f'[overflow-wrap:anywhere]">{_escape_html(filename)}</span>',
    ]

    if kind == "csv-to-excel":
        rows = data["sample_rows"]
        parts.append(
            f'<span class="text-[0.84rem] font-bold text-kv-muted">'
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
            f'<span class="text-[0.84rem] font-bold text-kv-muted">'
            f'{_format_bytes(size_bytes)} · {data["page_count"]} halaman</span>'
        )
        parts.append("</div>")

        estimate = int(data.get("estimated_seconds") or 0)
        if estimate > 0:
            parts.append(
                f'<p class="mt-2.5 mb-0 text-[0.84rem] font-bold text-kv-blue">'
                f"Perkiraan waktu konversi: ± {format_interval(estimate)}</p>"
            )

        table = _table_html(headers, rows)
        caption = (
            f"Menampilkan {len(raw_rows)} baris dari halaman pertama "
            f"(total {data['page_count']} halaman)."
        )

    parts.append(table)
    parts.append(f'<p class="m-0 text-[0.8rem] text-kv-subtle">{_escape_html(caption)}</p>')
    return "".join(parts)


def _overlay_html(percent, message, eta, elapsed) -> str:
    percent = max(0, min(100, int(round(percent))))
    eta_line = ""
    running_line = ""

    if percent >= 100:
        eta_line = (
            f'<p class="mt-2.5 mb-1 text-[0.82rem] font-semibold text-kv-faint">'
            f"Selesai dalam {format_interval(elapsed)}</p>"
        )
    elif eta is not None and eta >= 0:
        eta_line = (
            f'<p class="mt-2.5 mb-1 text-[0.82rem] font-semibold text-kv-faint">'
            f"± {format_interval(eta)} tersisa</p>"
        )

    if percent < 100 and elapsed and elapsed > 0:
        running_line = (
            '<p class="mt-0 mb-5 text-[0.82rem] font-semibold text-kv-faint">'
            f"berjalan {format_interval(elapsed)}</p>"
        )

    return (
        '<div class="fixed inset-0 z-[99999] grid place-items-center bg-[rgba(11,18,34,0.55)] p-5">'
        '<div class="w-[min(430px,100%)] rounded-[26px] bg-white p-[26px] text-left shadow-[0_26px_60px_rgba(8,15,32,0.35)]" '
        'role="dialog" aria-modal="true">'
        '<h2 class="m-0 p-0 text-[1.24rem] tracking-[-0.02em] text-kv-ink">Memproses file</h2>'
        f'<p class="mt-2.5 mb-5 text-[0.94rem] text-kv-muted">{_escape_html(message or "Memproses file")}</p>'
        f"{eta_line}{running_line}"
        '<div class="h-3 overflow-hidden rounded-full bg-kv-track">'
        '<div class="h-full rounded-[inherit] bg-gradient-to-r from-[#1b49d8] to-[#4b8bff] '
        f'transition-[width] duration-300 ease-out" style="width:{percent}%"></div>'
        "</div>"
        f'<p class="mt-3 mb-0 text-[0.9rem] font-extrabold text-kv-navy">{percent}%</p>'
        "</div></div>"
    )


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
    for key in ("output_bytes", "output_name", "output_sig", "output_kind"):
        session_state.pop(key, None)
    return path


def _drop_temp_input() -> None:
    path = streamlit.session_state.pop("input_path", None)
    streamlit.session_state.pop("upload_sig", None)

    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


@streamlit.cache_data(show_spinner="Memeriksa file...", max_entries=8)
def _cached_preview(kind: str, path: str, size: int, modification_time_nanoseconds: int):
    if kind == "csv-to-excel":
        return preview_csv_file(path)
    return preview_pdf_file(path)


def _navigate(page: str) -> None:
    streamlit.session_state["kv_page"] = page
    streamlit.rerun()


def _brand_html() -> str:
    return (
        '<div class="kv-brand">'
        '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" '
        'stroke-width="1.5" stroke="currentColor" class="kv-brand-icon">'
        '<path stroke-linecap="round" stroke-linejoin="round" '
        'd="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m.75 12 3 3m0 0 3-3m-3 3v-6m-1.5-9H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"/>'
        '</svg>'
        '<span class="kv-brand-text">'
        '<span class="kv-brand-name">Konverter</span>'
        '<span class="kv-brand-sub">PDF &amp; CSV → Excel</span>'
        '</span></div>'
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

    with streamlit.container(key="kv-navbar"):
        brand_col, *action_cols = streamlit.columns(
            [4] + [1] * len(slots), vertical_alignment="center", gap="small"
        )

        with brand_col:
            streamlit.markdown(_brand_html(), unsafe_allow_html=True)

        for slot, col in zip(slots, action_cols):
            with col:
                if slot == "back":
                    if streamlit.button(back_label, key="kv-nav-back", width="stretch"):
                        _navigate("home")
                elif slot == "history":
                    if streamlit.button("Riwayat", key="kv-nav-riwayat", width="stretch"):
                        _navigate("history")
                elif slot == "masuk":
                    if streamlit.button("Masuk", key="kv-nav-masuk", type="primary",
                                 width="stretch"):
                        _navigate("auth")
                elif slot == "keluar":
                    if streamlit.button("Keluar", key="kv-nav-keluar", width="stretch"):
                        store.sign_out()
                        _navigate("home")
                else:
                    streamlit.markdown(
                        '<span class="kv-nav-badge">Login belum aktif</span>',
                        unsafe_allow_html=True,
                    )


def _page_home(user, limit_mb: int) -> None:
    _brand_row(user)

    with streamlit.container(key="kv-hero"):
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

    with streamlit.container(key="kv-hero"):
        streamlit.markdown(_hero_html(subtitle), unsafe_allow_html=True)
        streamlit.markdown(_limit_banner_html(user, limit_mb), unsafe_allow_html=True)

        with streamlit.container(key="kv-converter"):
            streamlit.markdown(f'<p class="mb-3 mt-0 p-0 text-[1.15rem] font-black text-kv-ink">{_escape_html(title)}</p>', unsafe_allow_html=True)
            streamlit.markdown(_steps_html(active_step), unsafe_allow_html=True)

            uploaded = streamlit.file_uploader("File sumber", type=["pdf", "csv"], key="kv-upload")
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
                        streamlit.warning(str(error))
                    except Exception as error:
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
                    key="kv-download",
                )
                if user and streamlit.session_state.get("output_job"):
                    streamlit.caption("Hasil ini tersimpan di riwayat konversi akunmu.")


def _run_conversion(kind: str, user, uploaded, input_path: str, preview_data: dict) -> None:
    if not streamlit.button("Konversi Sekarang", type="primary", key="kv-convert"):
        return

    overlay = streamlit.empty()
    overlay.markdown(_overlay_html(0, "Menyiapkan file", None, 0), unsafe_allow_html=True)

    def on_update(percent, message, eta, elapsed):
        overlay.markdown(_overlay_html(percent, message, eta, elapsed), unsafe_allow_html=True)

    def on_wait(waited, position):
        overlay.markdown(
            _overlay_html(0, f"Menunggu antrean (posisi {position})", None, waited),
            unsafe_allow_html=True,
        )

    reporter = ProgressReporter(on_update)
    page_count = preview_data.get("page_count") if kind == "pdf-to-excel" else None
    output_name = f"{Path(uploaded.name).stem}.xlsx"

    file_descriptor, output_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(file_descriptor)

    streamlit.session_state["converting"] = True
    object_id = uuid4().hex
    job_id = None
    result_name = None
    started = time.perf_counter()

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
            streamlit.warning(f"Gagal menyimpan sumber ke riwayat: {error}")

    try:
        with conversion_slot(on_wait=on_wait):
            if kind == "csv-to-excel":
                convert_csv_to_excel(input_path, output_path, reporter)
            else:
                convert_pdf_to_excel(input_path, output_path, reporter, total_pages=page_count)

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
                store.finish_job(
                    job_id,
                    "completed",
                    result_name=result_name,
                    result_path=result_path,
                    output_size_bytes=len(result_bytes),
                    page_count=page_count,
                    row_count=preview_data.get("row_count") if kind == "csv-to-excel" else None,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception as error:
                streamlit.warning(f"Hasil tidak tersimpan ke riwayat: {error}")
    except ValueError as error:
        store.finish_job(job_id, "failed", error=str(error))
        streamlit.error(str(error))
    except QueueTimeout as error:
        store.finish_job(job_id, "failed", error=str(error))
        streamlit.warning(str(error))
    except Exception as error:
        store.finish_job(job_id, "failed", error=str(error))
        streamlit.error(f"Konversi gagal: {error}")
    finally:
        overlay.empty()
        streamlit.session_state["converting"] = False
        try:
            os.unlink(output_path)
        except OSError:
            pass


def _page_auth() -> None:
    _brand_row(None, back_label="← Kembali", show_login=False)

    with streamlit.container(key="kv-hero"):
        streamlit.markdown(
            _copy_html(
                "Akun",
                "Masuk untuk menyimpan riwayat.",
                f"Login membuka batas unggahan {USER_MAX_MB} MB dan menyimpan hasil konversi di riwayat akunmu.",
            ),
            unsafe_allow_html=True,
        )

        with streamlit.container(key="kv-converter"):
            tab_in, tab_up = streamlit.tabs(["Masuk", "Daftar"])

            with tab_in:
                email = streamlit.text_input("Email", key="kv-in-email")
                password = streamlit.text_input("Kata sandi", type="password", key="kv-in-password")

                if streamlit.button("Masuk", type="primary", key="kv-in-submit"):
                    if not email or not password:
                        streamlit.warning("Isi email dan kata sandi.")
                    else:
                        try:
                            store.sign_in(email.strip(), password)
                        except Exception as error:
                            streamlit.error(_auth_error(error))
                        else:
                            _navigate("home")

            with tab_up:
                email = streamlit.text_input("Email baru", key="kv-up-email")
                password = streamlit.text_input("Kata sandi baru (min. 8 karakter)", type="password", key="kv-up-password")

                if streamlit.button("Daftar", type="primary", key="kv-up-submit"):
                    if not email or len(password) < 8:
                        streamlit.warning("Isi email dan kata sandi minimal 8 karakter.")
                    else:
                        try:
                            result = store.sign_up(email.strip(), password)
                        except Exception as error:
                            streamlit.error(_auth_error(error))
                        else:
                            if result.get("needs_confirmation"):
                                streamlit.success(
                                    "Akun dibuat. Cek email untuk konfirmasi, lalu masuk di tab Masuk."
                                )
                            else:
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

    with streamlit.container(key="kv-hero"):
        streamlit.markdown(
            _copy_html(
                "Riwayat",
                "Konversi yang kamu simpan.",
                "Hasil dapat diunduh ulang kapan saja selama masih tersimpan.",
            ),
            unsafe_allow_html=True,
        )

        with streamlit.container(key="kv-converter"):
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
        "completed": "bg-kv-success-soft text-kv-success",
        "processing": "bg-kv-warn-soft text-kv-warn",
        "failed": "bg-[#ffe9ec] text-kv-danger",
    }.get(status, "bg-kv-th text-kv-muted")

    streamlit.markdown(
        '<div class="flex flex-wrap items-center gap-2.5 py-3">'
        f'<span class="rounded-full px-2.5 py-[5px] text-[0.72rem] font-black tracking-[0.03em] '
        f'{status_classes}">{_escape_html(_status_label(status))}</span>'
        f'<span class="font-extrabold text-kv-ink [overflow-wrap:anywhere]">'
        f'{_escape_html(job.get("original_name") or "-")}</span>'
        f'<span class="text-[0.84rem] font-bold text-kv-muted">'
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
            if streamlit.button("Ya, hapus", key=f"kv-del-confirm-{job['id']}"):
                streamlit.session_state.pop(f"kv-dl-data-{job['id']}", None)
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

    cache_key = f"kv-dl-data-{job['id']}"
    data = streamlit.session_state.get(cache_key)

    if not data:
        if streamlit.button("Siapkan unduhan", key=f"kv-dl-prep-{job['id']}"):
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
        key=f"kv-dl-btn-{job['id']}",
    )


user = store.current_user()

if user is None and streamlit.session_state.get("kv_page") == "history":
    streamlit.session_state["kv_page"] = "auth"

page = streamlit.session_state.setdefault("kv_page", "home")
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
