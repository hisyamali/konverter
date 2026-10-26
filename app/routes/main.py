"""Route HTTP untuk halaman utama, pratinjau file, konversi, dan polling progress."""

import io
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
from app.services.eta import ProgressReporter
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


def _log_preview_upload(conversion_type, filename, size_bytes, preview_id):
    current_app.logger.info("File uploaded for preview", extra={
        "context": {
            "conversion_type": conversion_type,
            "filename": filename,
            "size_bytes": size_bytes,
            "preview_id": preview_id,
        }
    })


def _log_conversion_start(job_id, preview_id, conversion_type, filename, pages):
    current_app.logger.info("Conversion started", extra={
        "context": {
            "job_id": job_id,
            "preview_id": preview_id,
            "conversion_type": conversion_type,
            "filename": filename,
            "pages": pages,
        }
    })


def _log_conversion_finished(job_id, preview_id, duration_ms, rows, output_size):
    current_app.logger.info("Conversion finished", extra={
        "context": {
            "job_id": job_id,
            "preview_id": preview_id,
            "duration_ms": duration_ms,
            "rows": rows,
            "output_size_bytes": output_size,
        }
    })


def _log_conversion_error(job_id, preview_id, error, error_type):
    current_app.logger.error("Conversion failed", extra={
        "context": {
            "job_id": job_id,
            "preview_id": preview_id,
            "error": str(error),
            "error_type": error_type,
        }
    }, exc_info=error)


def _log_rejection(message, job_id=None, preview_id=None, level="WARNING"):
    """Catat penolakan request (validasi upload/pratinjau/konversi) ke log."""
    context = {"reason": message}
    if job_id:
        context["job_id"] = job_id
    if preview_id:
        context["preview_id"] = preview_id

    if level == "ERROR":
        current_app.logger.error("Request rejected", extra={"context": context})
    else:
        current_app.logger.warning("Request rejected", extra={"context": context})


def _log_preview_error(error, filename, conversion_type):
    """Catat kegagalan pembacaan file saat pratinjau."""
    context = {
        "error": str(error),
        "error_type": type(error).__name__,
        "filename": filename,
        "conversion_type": conversion_type,
    }

    if isinstance(error, ValueError):
        current_app.logger.warning("Preview rejected", extra={"context": context})
    else:
        current_app.logger.error("Preview failed", extra={"context": context}, exc_info=True)


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
        _log_preview_error(error, original_name, conversion_type)
        _cleanup(input_path)
        return _reject(str(error), level="WARNING", log=False)
    except Exception as error:
        _log_preview_error(error, original_name, conversion_type)
        _cleanup(input_path)
        return _reject(f"Tidak dapat membaca file: {error}", level="ERROR", log=False)

    preview_id = uuid4().hex
    register_preview(preview_id, input_path, original_name, conversion_type, meta=preview_data)

    _sweep_stale()

    _log_preview_upload(conversion_type, original_name, input_path.stat().st_size, preview_id)

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
        return _reject(
            "File belum diunggah atau pratinjau kedaluwarsa. Unggah ulang file.",
            job_id,
            preview_id=preview_id,
        )

    input_path = preview["path"]
    original_name = preview["original_name"]
    conversion_type = preview["conversion_type"]
    page_count = preview.get("meta", {}).get("page_count")
    output_name = f"{Path(original_name).stem}-{uuid4().hex[:8]}.xlsx"
    output_path = current_app.config["OUTPUT_FOLDER"] / output_name

    create_job(job_id, "Menerima file")
    progress = _build_progress(job_id)

    start_time = time.perf_counter()
    rows = 0
    output_size = 0

    try:
        _log_conversion_start(job_id, preview_id, conversion_type, original_name, page_count)

        if conversion_type == "csv-to-excel":
            convert_csv_to_excel(input_path, output_path, progress)
        else:
            convert_pdf_to_excel(input_path, output_path, progress, total_pages=page_count)

        if output_path.exists():
            output_size = output_path.stat().st_size

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        _log_conversion_finished(job_id, preview_id, duration_ms, rows, output_size)

    except ValueError as error:
        _cleanup(input_path, output_path)
        delete_preview(preview_id)
        _log_conversion_error(job_id, preview_id, error, "ValueError")
        return _reject(str(error), job_id, preview_id=preview_id, level="ERROR", log=False)
    except Exception as error:
        _cleanup(input_path, output_path)
        delete_preview(preview_id)
        _log_conversion_error(job_id, preview_id, error, type(error).__name__)
        return _reject(f"Konversi gagal: {error}", job_id, preview_id=preview_id, level="ERROR", log=False)

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
    deleted_uploads = sweep_old_files(app.config["UPLOAD_FOLDER"])
    deleted_outputs = sweep_old_files(app.config["OUTPUT_FOLDER"])
    if deleted_uploads or deleted_outputs:
        current_app.logger.info("Sweep completed", extra={
            "context": {
                "deleted_uploads": deleted_uploads,
                "deleted_outputs": deleted_outputs,
            }
        })


def _build_progress(job_id):
    """Buat callback progress yang menghitung ETA lalu menyimpannya ke job store."""

    def handle_update(percent, message, eta, _elapsed):
        update_job(job_id, percent, message, eta_seconds=eta)

    def handle_phase(phase, percent):
        current_app.logger.info("Conversion phase", extra={
            "context": {
                "job_id": job_id,
                "phase": phase,
                "percent": percent,
            }
        })

    return ProgressReporter(handle_update, on_phase=handle_phase)


def _reject(message, job_id=None, preview_id=None, level="WARNING", log=True):
    """Kirim error JSON dan tandai job gagal bila request terkait proses konversi."""
    if log:
        _log_rejection(message, job_id=job_id, preview_id=preview_id, level=level)

    if job_id and get_job(job_id) is None:
        create_job(job_id, "Memvalidasi file")

    if job_id:
        finish_job(job_id, error=message)

    return jsonify({"error": message}), 400


@main_bp.app_errorhandler(404)
def not_found(error):
    """Catat 404 tanpa mengubah format respons bawaan Flask."""
    current_app.logger.warning("HTTP error", extra={
        "context": {
            "status": 404,
            "method": request.method,
            "path": request.path,
        }
    })
    return error.get_response()


@main_bp.app_errorhandler(500)
def internal_error(error):
    """Catat 500 dan kirim pesan JSON yang aman."""
    original = getattr(error, "original_exception", None) or error
    current_app.logger.error("HTTP error", extra={
        "context": {
            "status": 500,
            "method": request.method,
            "path": request.path,
            "error": str(original),
            "error_type": type(original).__name__,
        }
    }, exc_info=original)
    return jsonify({"error": "Terjadi kesalahan internal pada server."}), 500


@main_bp.app_errorhandler(413)
def file_too_large(_error):
    """Berikan pesan jelas saat ukuran upload melewati batas konfigurasi."""
    limit_mb = current_app.config.get("MAX_UPLOAD_MB", 128)
    message = f"Ukuran file melebihi batas {limit_mb} MB."
    current_app.logger.warning("Upload rejected", extra={
        "context": {
            "reason": message,
            "path": request.path,
            "content_length": request.content_length,
        }
    })
    return jsonify({"error": message}), 413
