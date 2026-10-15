"""Route HTTP untuk halaman utama, pratinjau file, konversi, dan polling progress."""

import io
import re
import time
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from app.services.converter import (
    convert_csv_to_excel,
    convert_pdf_to_excel,
    preview_csv_file,
    preview_pdf_file,
)
from app.services.previews import (
    delete_preview,
    get_preview,
    register_preview,
    sweep_old_files,
)
from app.services.progress import create_job, finish_job, get_job, update_job


main_bp = Blueprint("main", __name__)
ALLOWED_EXTENSIONS = {"csv", "pdf"}
EXCEL_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@main_bp.get("/")
def index():
    """Tampilkan halaman utama konverter."""
    return render_template("index.html")


@main_bp.get("/progress")
def progress_status():
    """Kirim status progress konversi agar frontend bisa memperbarui progress bar."""
    job_id = request.args.get("job_id", "").strip()
    job = get_job(job_id)

    if job is None:
        return jsonify({"percent": 0, "message": "Menyiapkan file", "done": False, "error": None, "eta_seconds": None, "elapsed_seconds": 0})

    return jsonify(job)


@main_bp.post("/preview")
def preview():
    """Validasi upload, simpan sementara, lalu kirim data pratinjau ke frontend."""
    conversion_type = request.form.get("conversion_type", "").strip()
    uploaded_file = request.files.get("file")

    if uploaded_file is None or uploaded_file.filename == "":
        return _reject("Pilih file terlebih dahulu.")

    original_name = secure_filename(uploaded_file.filename)
    extension = Path(original_name).suffix.lower().lstrip(".")

    if extension not in ALLOWED_EXTENSIONS:
        return _reject("Format file harus PDF atau CSV.")

    if conversion_type == "csv-to-excel" and extension != "csv":
        return _reject("Untuk CSV to Excel, upload file .csv.")

    if conversion_type == "pdf-to-excel" and extension != "pdf":
        return _reject("Untuk PDF to Excel, upload file .pdf.")

    if conversion_type not in {"csv-to-excel", "pdf-to-excel"}:
        return _reject("Pilih jenis konversi yang valid.")

    upload_name = f"{uuid4().hex}-{original_name}"
    input_path = current_app.config["UPLOAD_FOLDER"] / upload_name
    uploaded_file.save(input_path)

    try:
        if conversion_type == "csv-to-excel":
            preview_data = preview_csv_file(input_path)
        else:
            preview_data = preview_pdf_file(input_path)
    except ValueError as error:
        _cleanup(input_path)
        return _reject(str(error))
    except Exception as error:
        _cleanup(input_path)
        return _reject(f"Tidak dapat membaca file: {error}")

    preview_id = uuid4().hex
    register_preview(preview_id, input_path, original_name, conversion_type, meta=preview_data)

    _sweep_stale()

    return jsonify(
        {
            "preview_id": preview_id,
            "filename": original_name,
            "size_bytes": input_path.stat().st_size,
            "conversion_type": conversion_type,
            **preview_data,
        }
    )


@main_bp.post("/convert")
def convert():
    """Konversi file yang sudah dipratinjau menjadi Excel dan kirim hasil download."""
    job_id = request.form.get("job_id", "").strip() or uuid4().hex
    preview_id = request.form.get("preview_id", "").strip()

    preview = get_preview(preview_id)

    if preview is None:
        return _reject("File belum diunggah atau pratinjau kedaluwarsa. Unggah ulang file.", job_id)

    input_path = preview["path"]
    original_name = preview["original_name"]
    conversion_type = preview["conversion_type"]
    output_name = f"{Path(original_name).stem}-{uuid4().hex[:8]}.xlsx"
    output_path = current_app.config["OUTPUT_FOLDER"] / output_name

    create_job(job_id, "Menerima file")
    progress = _build_progress(job_id)

    try:
        if conversion_type == "csv-to-excel":
            convert_csv_to_excel(input_path, output_path, progress)
        else:
            convert_pdf_to_excel(input_path, output_path, progress, total_pages=preview.get("meta", {}).get("page_count"))
    except ValueError as error:
        _cleanup(input_path, output_path)
        delete_preview(preview_id)
        return _reject(str(error), job_id)
    except Exception as error:
        _cleanup(input_path, output_path)
        delete_preview(preview_id)
        return _reject(f"Konversi gagal: {error}", job_id)

    finish_job(job_id)

    payload = output_path.read_bytes()
    _cleanup(input_path, output_path)
    delete_preview(preview_id)

    _sweep_stale()

    return send_file(
        io.BytesIO(payload),
        as_attachment=True,
        download_name=output_name,
        mimetype=EXCEL_MIMETYPE,
    )


def _cleanup(*paths):
    """Hapus file sementara tanpa menggagalkan request jika file sudah tidak ada."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _sweep_stale():
    """Bersihkan file upload/output lama agar storage tidak menumpuk."""
    app = current_app
    sweep_old_files(app.config["UPLOAD_FOLDER"])
    sweep_old_files(app.config["OUTPUT_FOLDER"])


def _build_progress(job_id):
    """Buat callback progress yang menghitung ETA lalu menyimpannya ke job store."""
    started = time.monotonic()
    state = {"eta": None, "phase": None, "phase_started": 0.0, "phase_percent": 0}

    def report(percent, message=None):
        elapsed = time.monotonic() - started
        phase = _phase_key(message)

        if phase != state["phase"]:
            state["phase"] = phase
            state["phase_started"] = elapsed
            state["phase_percent"] = percent
            state["eta"] = None

        eta = _estimate_eta(percent, elapsed, message, state)
        state["eta"] = eta
        update_job(job_id, percent, message, eta_seconds=eta)

    return report


_PAGE_PROGRESS_RE = re.compile(r"Halaman (\d+) dari halaman (\d+)")


def _phase_key(message):
    """Kelompokkan pesan progress ke fase besar untuk perhitungan ETA yang stabil."""
    if not message:
        return None

    if message.startswith("Halaman "):
        return "reading"

    if message.startswith("Menyusun "):
        return "normalize"

    if message.startswith("Menulis "):
        return "write"

    return None


def _estimate_eta(percent, elapsed, message, state):
    """Hitung sisa waktu berdasarkan halaman PDF atau kenaikan persentase fase."""
    if percent <= 0 or percent >= 100:
        return None

    remaining = None
    match = _PAGE_PROGRESS_RE.match(message) if message else None

    if match:
        pages_read = int(match.group(1))
        total_pages = int(match.group(2))

        if pages_read > 0:
            remaining = elapsed * (total_pages - pages_read) / pages_read

    if remaining is None:
        phase_elapsed = max(elapsed - state["phase_started"], 0.001)
        span = max(percent - state["phase_percent"], 1)
        remaining = phase_elapsed * (100 - percent) / span

    previous = state["eta"]

    if previous is None:
        return int(max(remaining, 0))

    alpha = 0.7 if remaining >= previous else 0.5
    return int(round(max(alpha * previous + (1 - alpha) * remaining, 0)))


def _reject(message, job_id=None):
    """Kirim error JSON dan tandai job gagal bila request terkait proses konversi."""
    if job_id and get_job(job_id) is None:
        create_job(job_id, "Memvalidasi file")

    if job_id:
        finish_job(job_id, error=message)

    return jsonify({"error": message}), 400


@main_bp.app_errorhandler(413)
def file_too_large(_error):
    """Berikan pesan jelas saat ukuran upload melewati batas konfigurasi."""
    limit_mb = current_app.config.get("MAX_UPLOAD_MB", 128)
    return jsonify({"error": f"Ukuran file melebihi batas {limit_mb} MB."}), 413
