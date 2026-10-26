import os
from pathlib import Path

os.environ.setdefault("KONVERTER_MAX_WORKERS", "2")
os.environ.setdefault("KONVERTER_MAX_UPLOAD_MB", "50")

import tempfile

import pandas as pd
import streamlit as st

from app.services.converter import (
    convert_csv_to_excel,
    convert_pdf_to_excel,
    preview_csv_file,
    preview_pdf_file,
)

EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
    state.pop("output_bytes", None)
    state.pop("output_name", None)
    return path


@st.cache_data(show_spinner=False)
def _preview(kind: str, path: str, size: int, mtime_ns: int):
    if kind == "csv-to-excel":
        return preview_csv_file(path)
    return preview_pdf_file(path)


st.set_page_config(page_title="Konverter PDF/CSV ke Excel", layout="centered")

st.title("Konverter PDF/CSV ke Excel")
st.markdown("Unggah file, lihat pratinjau, lalu konversi dan unduh hasil .xlsx.")

kind = st.radio(
    "Jenis konversi",
    ["pdf-to-excel", "csv-to-excel"],
    format_func=lambda value: "PDF ke Excel" if value == "pdf-to-excel" else "CSV ke Excel",
    horizontal=True,
)

uploaded = st.file_uploader("Unggah file", type=["pdf", "csv"])

if uploaded is not None:
    input_path = _save_upload(uploaded)
    file_stat = Path(input_path).stat()
    extension = Path(uploaded.name).suffix.lower().lstrip(".")
    expected = "pdf" if kind == "pdf-to-excel" else "csv"

    if extension != expected:
        st.warning(f"Untuk {kind}, unggah file .{expected} (file saat ini .{extension}).")
    else:
        try:
            data = _preview(kind, input_path, file_stat.st_size, file_stat.st_mtime_ns)
        except ValueError as exc:
            st.warning(str(exc))
        except Exception as exc:
            st.error(f"Tidak dapat membaca file: {exc}")
        else:
            if kind == "csv-to-excel":
                st.caption(f"{data['row_count']} baris, {data['column_count']} kolom")
                if data["sample_rows"]:
                    st.dataframe(pd.DataFrame(data["sample_rows"], columns=data["headers"]))
                page_count = None
            else:
                est = data.get("estimated_seconds") or 0
                caption = f"{data['page_count']} halaman"
                if est:
                    caption += f" · estimasi ±{est} detik"
                st.caption(caption)
                if data["sample_rows"]:
                    st.dataframe(pd.DataFrame(data["sample_rows"]))
                page_count = data.get("page_count")

            if st.button("Konversi Sekarang", type="primary"):
                progress_bar = st.progress(0, text="Menyiapkan file")
                status = st.status("Memproses file", expanded=True)

                def report(percent, message=None):
                    progress_bar.progress(min(int(percent), 100), text=message or "Memproses...")

                fd, output_path = tempfile.mkstemp(suffix=".xlsx")
                os.close(fd)

                try:
                    if kind == "csv-to-excel":
                        convert_csv_to_excel(input_path, output_path, report)
                    else:
                        convert_pdf_to_excel(input_path, output_path, report, total_pages=page_count)

                    output_bytes = Path(output_path).read_bytes()
                    st.session_state["output_bytes"] = output_bytes
                    st.session_state["output_name"] = f"{Path(uploaded.name).stem}.xlsx"
                    progress_bar.progress(100, text="Selesai")
                    status.update(label="Selesai", state="complete", expanded=False)
                except ValueError as exc:
                    status.update(label="Gagal", state="error")
                    st.error(str(exc))
                except Exception as exc:
                    status.update(label="Gagal", state="error")
                    st.error(f"Konversi gagal: {exc}")
                finally:
                    try:
                        os.unlink(output_path)
                    except OSError:
                        pass

if st.session_state.get("output_bytes"):
    st.download_button(
        "Unduh Excel",
        data=st.session_state["output_bytes"],
        file_name=st.session_state.get("output_name", "hasil.xlsx"),
        mime=EXCEL_MIME,
        type="primary",
    )
