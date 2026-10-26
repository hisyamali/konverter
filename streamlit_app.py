import html
import os
import re
import tempfile
from pathlib import Path

os.environ.setdefault("KONVERTER_MAX_WORKERS", "1" if os.name == "nt" else "2")
os.environ.setdefault("KONVERTER_MAX_UPLOAD_MB", "50")

import streamlit as st

from app.services.converter import (
    convert_csv_to_excel,
    convert_pdf_to_excel,
    preview_csv_file,
    preview_pdf_file,
)
from app.services.eta import ProgressReporter, format_interval

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSS_PATH = Path(__file__).parent / "app" / "static" / "css" / "streamlit.css"

st.set_page_config(page_title="Konverter PDF/CSV ke Excel", layout="centered")

if CSS_PATH.exists():
    st.markdown(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def _esc(value) -> str:
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
    head = "".join(
        f"<th>{_esc(header) or f'Kolom {index + 1}'}</th>" for index, header in enumerate(headers)
    )
    body = "".join(
        "<tr>" + "".join(f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    return (
        '<div class="kv-preview-scroll"><table class="kv-preview-table">'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def _hero_html() -> str:
    return (
        '<div class="kv-hero-copy">'
        '<p class="kv-eyebrow">Web &amp; Mobile Converter</p>'
        "<h1>Ubah PDF atau CSV menjadi Excel dalam beberapa detik.</h1>"
        '<p class="kv-description">Upload file, lihat pratinjau, lalu konversi dan '
        "download hasilnya sebagai file .xlsx.</p>"
        "</div>"
    )


def _steps_html(active: int) -> str:
    first = " is-active" if active == 1 else ""
    second = " is-active" if active == 2 else ""
    return (
        '<ol class="kv-steps" aria-label="Langkah">'
        f'<li class="kv-step{first}"><span>1</span> Unggah &amp; pratinjau</li>'
        f'<li class="kv-step{second}"><span>2</span> Konversi</li>'
        "</ol>"
    )


def _feature_html() -> str:
    return (
        '<section class="kv-feature-grid" aria-label="Fitur aplikasi">'
        "<article><span>01</span><h2>PDF ke Excel</h2>"
        "<p>Membaca tabel dari PDF dan menggabungkan hasilnya dalam satu sheet Excel.</p></article>"
        "<article><span>02</span><h2>CSV ke Excel</h2>"
        "<p>Mendeteksi pemisah koma atau titik koma secara otomatis.</p></article>"
        "<article><span>03</span><h2>Pratinjau Sebelum Konversi</h2>"
        "<p>Cek isi file (kolom, baris, jumlah halaman) sebelum proses konversi berjalan.</p></article>"
        "</section>"
    )


def _preview_html(kind: str, data: dict, filename: str, size_bytes: int) -> str:
    parts = [
        '<div class="kv-preview-meta">',
        f'<span class="kv-badge-file">{_esc(filename)}</span>',
    ]

    if kind == "csv-to-excel":
        rows = data["sample_rows"]
        parts.append(f'<span class="kv-facts">{_format_bytes(size_bytes)} · {data["row_count"]} baris</span>')
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
            f'<span class="kv-facts">{_format_bytes(size_bytes)} · {data["page_count"]} halaman</span>'
        )
        parts.append("</div>")

        estimate = int(data.get("estimated_seconds") or 0)
        if estimate > 0:
            parts.append(
                f'<p class="kv-estimate">Perkiraan waktu konversi: ± {format_interval(estimate)}</p>'
            )

        table = _table_html(headers, rows)
        caption = (
            f"Menampilkan {len(raw_rows)} baris dari halaman pertama "
            f"(total {data['page_count']} halaman)."
        )

    parts.append(table)
    parts.append(f'<p class="kv-preview-caption">{_esc(caption)}</p>')
    return "".join(parts)


def _overlay_html(percent, message, eta, elapsed) -> str:
    pct = max(0, min(100, int(round(percent))))
    eta_line = ""
    running_line = ""

    if pct >= 100:
        eta_line = f'<p class="kv-loading-eta">Selesai dalam {format_interval(elapsed)}</p>'
    elif eta is not None and eta >= 0:
        eta_line = f'<p class="kv-loading-eta">± {format_interval(eta)} tersisa</p>'

    if pct < 100 and elapsed and elapsed > 0:
        running_line = f'<p class="kv-loading-eta-running">berjalan {format_interval(elapsed)}</p>'

    return (
        '<div class="kv-overlay">'
        '<div class="kv-loading-panel" role="dialog" aria-modal="true">'
        '<h2 class="kv-loading-title">Memproses file</h2>'
        f'<p class="kv-loading-message">{_esc(message or "Memproses file")}</p>'
        f"{eta_line}{running_line}"
        '<div class="kv-progress-track">'
        f'<div class="kv-progress-bar" style="width:{pct}%"></div>'
        "</div>"
        f'<p class="kv-loading-percent">{pct}%</p>'
        "</div></div>"
    )


def _signature(uploaded) -> str:
    return f"{uploaded.name}:{uploaded.size}:{uploaded.type}"


def _save_upload(uploaded) -> str:
    state = st.session_state
    sig = _signature(uploaded)

    if state.get("upload_sig") == sig and state.get("input_path"):
        return state["input_path"]

    old = state.get("input_path")
    if old:
        try:
            os.unlink(old)
        except OSError:
            pass

    suffix = Path(uploaded.name).suffix.lower() or ".bin"
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as handle:
        handle.write(uploaded.getvalue())

    state["input_path"] = path
    state["upload_sig"] = sig
    for key in ("output_bytes", "output_name", "output_sig", "output_kind"):
        state.pop(key, None)
    return path


@st.cache_data(show_spinner="Memeriksa file…", max_entries=8)
def _preview(kind: str, path: str, size: int, mtime_ns: int):
    if kind == "csv-to-excel":
        return preview_csv_file(path)
    return preview_pdf_file(path)


active_step = 2 if st.session_state.get("converting") else 1

with st.container(key="kv-hero"):
    st.markdown(_hero_html(), unsafe_allow_html=True)

    with st.container(key="kv-converter"):
        st.markdown(_steps_html(active_step), unsafe_allow_html=True)

        kind = st.selectbox(
            "Jenis konversi",
            ["pdf-to-excel", "csv-to-excel"],
            format_func=lambda value: "PDF ke Excel" if value == "pdf-to-excel" else "CSV ke Excel",
            key="kv-kind",
        )

        uploaded = st.file_uploader("File sumber", type=["pdf", "csv"], key="kv-upload")

        preview_data = None

        if uploaded is not None:
            input_path = _save_upload(uploaded)
            file_stat = Path(input_path).stat()
            extension = Path(uploaded.name).suffix.lower().lstrip(".")
            expected = "pdf" if kind == "pdf-to-excel" else "csv"

            if extension != expected:
                st.warning(f"Untuk {kind}, unggah file .{expected} (file saat ini .{extension}).")
            else:
                try:
                    preview_data = _preview(kind, input_path, file_stat.st_size, file_stat.st_mtime_ns)
                except ValueError as exc:
                    st.warning(str(exc))
                except Exception as exc:
                    st.error(f"Tidak dapat membaca file: {exc}")
                else:
                    st.markdown(
                        _preview_html(kind, preview_data, uploaded.name, file_stat.st_size),
                        unsafe_allow_html=True,
                    )

                    if st.button("Konversi Sekarang", type="primary", key="kv-convert"):
                        overlay = st.empty()
                        overlay.markdown(
                            _overlay_html(0, "Menyiapkan file", None, 0), unsafe_allow_html=True
                        )

                        def on_update(percent, message, eta, elapsed):
                            overlay.markdown(
                                _overlay_html(percent, message, eta, elapsed),
                                unsafe_allow_html=True,
                            )

                        reporter = ProgressReporter(on_update)
                        page_count = preview_data.get("page_count") if kind == "pdf-to-excel" else None

                        fd, output_path = tempfile.mkstemp(suffix=".xlsx")
                        os.close(fd)

                        st.session_state["converting"] = True
                        try:
                            if kind == "csv-to-excel":
                                convert_csv_to_excel(input_path, output_path, reporter)
                            else:
                                convert_pdf_to_excel(
                                    input_path, output_path, reporter, total_pages=page_count
                                )

                            st.session_state["output_bytes"] = Path(output_path).read_bytes()
                            st.session_state["output_name"] = f"{Path(uploaded.name).stem}.xlsx"
                            st.session_state["output_sig"] = st.session_state.get("upload_sig")
                            st.session_state["output_kind"] = kind
                        except ValueError as exc:
                            st.error(str(exc))
                        except Exception as exc:
                            st.error(f"Konversi gagal: {exc}")
                        finally:
                            overlay.empty()
                            st.session_state["converting"] = False
                            try:
                                os.unlink(output_path)
                            except OSError:
                                pass

        output_ready = (
            st.session_state.get("output_bytes")
            and st.session_state.get("output_sig") == st.session_state.get("upload_sig")
            and st.session_state.get("output_kind") == kind
        )

        if output_ready:
            st.download_button(
                "Unduh Excel",
                data=st.session_state["output_bytes"],
                file_name=st.session_state.get("output_name", "hasil.xlsx"),
                mime=EXCEL_MIME,
                type="primary",
                key="kv-download",
            )

st.markdown(_feature_html(), unsafe_allow_html=True)
