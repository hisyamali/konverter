import html
import os
import re
import tempfile
import time
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("KONVERTER_MAX_WORKERS", "1" if os.name == "nt" else "2")
os.environ.setdefault("KONVERTER_MAX_UPLOAD_MB", "50")

import streamlit as st

from app.services import supabase_store as sb
from app.services.converter import (
    convert_csv_to_excel,
    convert_pdf_to_excel,
    preview_csv_file,
    preview_pdf_file,
)
from app.services.eta import ProgressReporter, format_interval

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSS_PATH = Path(__file__).parent / "app" / "static" / "css" / "streamlit.css"

# Batas ukuran unggahan: tamu (tanpa login) vs pengguna yang login.
GUEST_MAX_MB = int(os.environ.get("KONVERTER_GUEST_MAX_UPLOAD_MB", "10") or "10")
USER_MAX_MB = int(os.environ.get("KONVERTER_USER_MAX_UPLOAD_MB", "50") or "50")

st.set_page_config(
    page_title="Konverter PDF/CSV ke Excel",
    layout="centered",
    initial_sidebar_state="collapsed",
)

if CSS_PATH.exists():
    st.markdown(f"<style>{CSS_PATH.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Helper tampilan
# ---------------------------------------------------------------------------
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


def _hero_html(subtitle: str = "") -> str:
    description = subtitle or (
        "Unggah file, lihat pratinjau, lalu konversi dan download hasilnya sebagai file .xlsx."
    )
    return (
        '<div class="kv-hero-copy">'
        '<p class="kv-eyebrow">Web &amp; Mobile Converter</p>'
        "<h1>Ubah PDF atau CSV menjadi Excel dalam beberapa detik.</h1>"
        f'<p class="kv-description">{_esc(description)}</p>'
        "</div>"
    )


def _steps_html(active: int) -> str:
    labels = ["Pilih &amp; unggah", "Konversi", "Unduh"]
    items = "".join(
        f'<li class="kv-step{" is-active" if index + 1 == active else ""}">'
        f"<span>{index + 1}</span> {label}</li>"
        for index, label in enumerate(labels)
    )
    return f'<ol class="kv-steps kv-steps-3" aria-label="Langkah">{items}</ol>'


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


def _limit_banner_html(user, limit_mb: int) -> str:
    if user:
        return (
            '<div class="kv-limit-banner is-user">'
            f'<strong>{_esc(user.get("email") or "Pengguna")}</strong> · '
            f"batas unggahan <strong>{limit_mb} MB</strong> · hasil otomatis tersimpan di riwayat."
            "</div>"
        )

    return (
        '<div class="kv-limit-banner">'
        '<span class="kv-limit-tag">Mode tamu</span> '
        f"Batas unggahan <strong>{limit_mb} MB</strong> · file langsung dihapus setelah dikonversi. "
        f"Masuk untuk batas <strong>{USER_MAX_MB} MB</strong> dan riwayat konversi."
        "</div>"
    )


def _tool_cards_html() -> str:
    return (
        '<div class="kv-tool-grid">'
        '<article class="kv-tool-card"><span class="kv-tool-icon">PDF</span>'
        "<h3>PDF ke Excel</h3><p>Gabungkan tabel dari seluruh halaman menjadi satu sheet Excel.</p></article>"
        '<article class="kv-tool-card"><span class="kv-tool-icon">CSV</span>'
        "<h3>CSV ke Excel</h3><p>Deteksi pemisah otomatis, langsung jadi .xlsx yang rapi.</p></article>"
        "</div>"
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


def _status_label(status: str) -> str:
    return {
        "completed": "Selesai",
        "processing": "Diproses",
        "failed": "Gagal",
    }.get(status, status or "-")


# ---------------------------------------------------------------------------
# State upload
# ---------------------------------------------------------------------------
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


def _drop_temp_input() -> None:
    """Hapus file sementara unggahan (dipakai mode tamu setelah konversi)."""
    path = st.session_state.pop("input_path", None)
    st.session_state.pop("upload_sig", None)

    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


@st.cache_data(show_spinner="Memeriksa file...", max_entries=8)
def _preview(kind: str, path: str, size: int, mtime_ns: int):
    if kind == "csv-to-excel":
        return preview_csv_file(path)
    return preview_pdf_file(path)


# ---------------------------------------------------------------------------
# Navigasi halaman (alur ala iLovePDF)
# ---------------------------------------------------------------------------
def _go(page: str) -> None:
    st.session_state["kv_page"] = page
    st.rerun()


def _render_nav(user) -> None:
    configured = sb.is_configured()
    columns = st.columns([2.6, 1, 1, 1.3], vertical_alignment="center")

    with columns[0]:
        st.markdown(
            '<div class="kv-brand"><span class="kv-brand-mark">K</span>'
            '<span class="kv-brand-text">Konverter<small>PDF &amp; CSV → Excel</small></span></div>',
            unsafe_allow_html=True,
        )

    with columns[1]:
        if st.button("Alat", key="kv-nav-alat"):
            _go("home")

    with columns[2]:
        if user and st.button("Riwayat", key="kv-nav-riwayat"):
            _go("history")

    with columns[3]:
        if not configured:
            st.markdown('<span class="kv-nav-note">Login belum aktif</span>', unsafe_allow_html=True)
        elif user:
            if st.button("Keluar", key="kv-nav-auth"):
                sb.sign_out()
                _go("home")
        elif st.button("Masuk", key="kv-nav-auth"):
            _go("auth")


def _page_home(user, limit_mb: int) -> None:
    with st.container(key="kv-hero"):
        st.markdown(_hero_html(), unsafe_allow_html=True)
        st.markdown(_limit_banner_html(user, limit_mb), unsafe_allow_html=True)

        st.markdown('<p class="kv-section-title">Pilih alat</p>', unsafe_allow_html=True)
        st.markdown(_tool_cards_html(), unsafe_allow_html=True)

        pick = st.columns(2)
        with pick[0]:
            if st.button("PDF ke Excel", type="primary", key="kv-pick-pdf"):
                _go("pdf")
        with pick[1]:
            if st.button("CSV ke Excel", type="primary", key="kv-pick-csv"):
                _go("csv")

        if user:
            if st.button("Lihat riwayat konversi", key="kv-home-history"):
                _go("history")

    st.markdown(_feature_html(), unsafe_allow_html=True)

    if not sb.is_configured():
        st.caption(
            "Riwayat & login (Supabase) belum dikonfigurasi. Isi `.streamlit/secrets.toml` "
            "lalu jalankan `supabase/schema.sql` untuk mengaktifkannya."
        )


def _page_tool(kind: str, user, limit_mb: int) -> None:
    title = "PDF ke Excel" if kind == "pdf-to-excel" else "CSV ke Excel"
    subtitle = (
        "Unggah PDF, cek pratinjau tabelnya, lalu unduh hasil Excel."
        if kind == "pdf-to-excel"
        else "Unggah CSV, cek pratinjau barisnya, lalu unduh hasil Excel."
    )
    active_step = 3 if st.session_state.get("output_kind") == kind and st.session_state.get(
        "output_sig"
    ) == st.session_state.get("upload_sig") else (2 if st.session_state.get("converting") else 1)

    if st.button("← Pilih alat lain", key="kv-nav-back"):
        _go("home")

    with st.container(key="kv-hero"):
        st.markdown(_hero_html(subtitle), unsafe_allow_html=True)
        st.markdown(_limit_banner_html(user, limit_mb), unsafe_allow_html=True)

        with st.container(key="kv-converter"):
            st.markdown(f'<p class="kv-tool-heading">{_esc(title)}</p>', unsafe_allow_html=True)
            st.markdown(_steps_html(active_step), unsafe_allow_html=True)

            uploaded = st.file_uploader("File sumber", type=["pdf", "csv"], key="kv-upload")
            preview_data = None

            if uploaded is None:
                # Tidak ada file: pastikan sisa file sementara dibersihkan.
                _drop_temp_input()

            if uploaded is not None:
                expected = "pdf" if kind == "pdf-to-excel" else "csv"
                extension = Path(uploaded.name).suffix.lower().lstrip(".")
                size_mb = uploaded.size / (1024 * 1024)

                if extension != expected:
                    st.warning(f"Untuk {title}, unggah file .{expected} (file saat ini .{extension}).")
                elif size_mb > limit_mb:
                    st.warning(
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
                        _run_conversion(kind, user, uploaded, input_path, preview_data)

            output_ready = (
                st.session_state.get("output_bytes")
                and st.session_state.get("output_sig") == st.session_state.get("upload_sig")
                and st.session_state.get("output_kind") == kind
            )

            if output_ready and uploaded is not None:
                st.download_button(
                    "Unduh Excel",
                    data=st.session_state["output_bytes"],
                    file_name=st.session_state.get("output_name", "hasil.xlsx"),
                    mime=EXCEL_MIME,
                    type="primary",
                    key="kv-download",
                )
                if user and st.session_state.get("output_job"):
                    st.caption("Hasil ini tersimpan di riwayat konversi akunmu.")


def _run_conversion(kind: str, user, uploaded, input_path: str, preview_data: dict) -> None:
    if not st.button("Konversi Sekarang", type="primary", key="kv-convert"):
        return

    overlay = st.empty()
    overlay.markdown(_overlay_html(0, "Menyiapkan file", None, 0), unsafe_allow_html=True)

    def on_update(percent, message, eta, elapsed):
        overlay.markdown(_overlay_html(percent, message, eta, elapsed), unsafe_allow_html=True)

    reporter = ProgressReporter(on_update)
    page_count = preview_data.get("page_count") if kind == "pdf-to-excel" else None
    output_name = f"{Path(uploaded.name).stem}.xlsx"

    fd, output_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)

    st.session_state["converting"] = True
    object_id = uuid4().hex
    job_id = None
    result_name = None
    started = time.perf_counter()

    if user:
        try:
            source_path = sb.object_path(user["id"], object_id, uploaded.name)
            sb.upload_file(
                sb.SOURCE_BUCKET,
                source_path,
                uploaded.getvalue(),
                uploaded.type or "application/octet-stream",
            )
            job_id = sb.create_job(
                user["id"], kind, uploaded.name, uploaded.size, source_path
            )
        except Exception as exc:
            st.warning(f"Gagal menyimpan sumber ke riwayat: {exc}")

    try:
        if kind == "csv-to-excel":
            convert_csv_to_excel(input_path, output_path, reporter)
        else:
            convert_pdf_to_excel(input_path, output_path, reporter, total_pages=page_count)

        result_bytes = Path(output_path).read_bytes()
        st.session_state["output_bytes"] = result_bytes
        st.session_state["output_name"] = output_name
        st.session_state["output_sig"] = st.session_state.get("upload_sig")
        st.session_state["output_kind"] = kind
        st.session_state["output_job"] = job_id

        if user and job_id:
            result_name = f"{Path(output_name).stem}-{object_id[:8]}.xlsx"
            try:
                result_path = sb.object_path(user["id"], object_id, result_name)
                sb.upload_file(sb.RESULT_BUCKET, result_path, result_bytes, EXCEL_MIME)
                sb.finish_job(
                    job_id,
                    "completed",
                    result_name=result_name,
                    result_path=result_path,
                    output_size_bytes=len(result_bytes),
                    page_count=page_count,
                    row_count=preview_data.get("row_count") if kind == "csv-to-excel" else None,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            except Exception as exc:
                st.warning(f"Hasil tidak tersimpan ke riwayat: {exc}")
    except ValueError as exc:
        sb.finish_job(job_id, "failed", error=str(exc))
        st.error(str(exc))
    except Exception as exc:
        sb.finish_job(job_id, "failed", error=str(exc))
        st.error(f"Konversi gagal: {exc}")
    finally:
        overlay.empty()
        st.session_state["converting"] = False
        try:
            os.unlink(output_path)
        except OSError:
            pass


def _page_auth() -> None:
    if st.button("← Kembali", key="kv-nav-back"):
        _go("home")

    with st.container(key="kv-hero"):
        st.markdown(
            '<div class="kv-hero-copy"><p class="kv-eyebrow">Akun</p>'
            "<h1>Masuk untuk menyimpan riwayat.</h1>"
            '<p class="kv-description">Login membuka batas unggahan '
            f"{USER_MAX_MB} MB dan menyimpan hasil konversi di riwayat akunmu.</p></div>",
            unsafe_allow_html=True,
        )

        with st.container(key="kv-converter"):
            tab_in, tab_up = st.tabs(["Masuk", "Daftar"])

            with tab_in:
                email = st.text_input("Email", key="kv-in-email")
                password = st.text_input("Kata sandi", type="password", key="kv-in-password")

                if st.button("Masuk", type="primary", key="kv-in-submit"):
                    if not email or not password:
                        st.warning("Isi email dan kata sandi.")
                    else:
                        try:
                            sb.sign_in(email.strip(), password)
                        except Exception as exc:
                            st.error(_auth_error(exc))
                        else:
                            _go("home")

            with tab_up:
                email = st.text_input("Email baru", key="kv-up-email")
                password = st.text_input("Kata sandi baru (min. 6 karakter)", type="password", key="kv-up-password")

                if st.button("Daftar", type="primary", key="kv-up-submit"):
                    if not email or len(password) < 6:
                        st.warning("Isi email dan kata sandi minimal 6 karakter.")
                    else:
                        try:
                            result = sb.sign_up(email.strip(), password)
                        except Exception as exc:
                            st.error(_auth_error(exc))
                        else:
                            if result.get("needs_confirmation"):
                                st.success(
                                    "Akun dibuat. Cek email untuk konfirmasi, lalu masuk di tab Masuk."
                                )
                            else:
                                _go("home")


def _auth_error(exc: Exception) -> str:
    message = getattr(exc, "message", None) or str(exc)
    if "Invalid login credentials" in message:
        return "Email atau kata sandi salah."
    if "User already registered" in message:
        return "Email ini sudah terdaftar. Coba masuk."
    if "Email not confirmed" in message:
        return "Email belum dikonfirmasi. Cek kotak masuk untuk tautan verifikasi."
    return f"Gagal: {message}"


def _page_history(user) -> None:
    with st.container(key="kv-hero"):
        st.markdown(
            '<div class="kv-hero-copy"><p class="kv-eyebrow">Riwayat</p>'
            "<h1>Konversi yang kamu simpan.</h1>"
            '<p class="kv-description">Hasil dapat diunduh ulang kapan saja selama masih tersimpan.</p></div>',
            unsafe_allow_html=True,
        )

        with st.container(key="kv-converter"):
            jobs = sb.list_jobs(user["id"])

            if not jobs:
                st.info("Belum ada riwayat konversi. Mulai dari halaman Alat.")
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

    st.markdown(
        '<div class="kv-history-item">'
        f'<span class="kv-job-status is-{status}">{_esc(_status_label(status))}</span>'
        f'<span class="kv-job-name">{_esc(job.get("original_name") or "-")}</span>'
        f'<span class="kv-facts">{" · ".join(_esc(item) for item in facts)}</span>'
        "</div>",
        unsafe_allow_html=True,
    )

    if status != "completed":
        if job.get("error"):
            st.caption(f"Alasan: {job['error']}")
        return

    actions = st.columns([1, 1, 2])
    with actions[0]:
        url = sb.signed_url(sb.RESULT_BUCKET, job["result_path"]) if job.get("result_path") else None
        if url:
            st.link_button("Unduh", url)
        else:
            st.caption("Hasil tidak tersedia")
    with actions[1]:
        with st.popover("Hapus"):
            st.write("Hapus konversi ini beserta filenya?")
            if st.button("Ya, hapus", key=f"kv-del-confirm-{job['id']}"):
                sb.delete_job(user["id"], job)
                st.rerun()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
user = sb.current_user()

if user is None and st.session_state.get("kv_page") == "history":
    st.session_state["kv_page"] = "auth"

page = st.session_state.setdefault("kv_page", "home")
limit_mb = USER_MAX_MB if user else GUEST_MAX_MB

st.markdown('<div class="kv-topbar">', unsafe_allow_html=True)
_render_nav(user)
st.markdown("</div>", unsafe_allow_html=True)

if page in ("pdf", "csv"):
    _page_tool("pdf-to-excel" if page == "pdf" else "csv-to-excel", user, limit_mb)
elif page == "history":
    _page_history(user)
elif page == "auth":
    if not sb.is_configured():
        st.info("Login belum dikonfigurasi. Isi `.streamlit/secrets.toml` dengan URL & anon key Supabase.")
    else:
        _page_auth()
else:
    _page_home(user, limit_mb)
