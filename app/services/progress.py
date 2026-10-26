"""Penyimpanan progress konversi sementara berbasis memori untuk polling frontend."""

import threading
import time

from flask import current_app

MAX_AGE_SECONDS = 600

_lock = threading.Lock()
_jobs = {}


def _log_job(action, job_id, **context):
    if current_app:
        current_app.logger.info(f"Job {action}", extra={"context": {"job_id": job_id, **context}})


def create_job(job_id, message="Menyiapkan file"):
    """Daftarkan job baru dengan status awal 0 persen."""
    with _lock:
        _purge_expired()
        _jobs[job_id] = {
            "percent": 0,
            "message": message,
            "done": False,
            "error": None,
            "eta_seconds": None,
            "started_at": time.time(),
            "updated_at": time.time(),
        }

    _log_job("create", job_id, message=message)


def update_job(job_id, percent, message=None, eta_seconds=None):
    """Perbarui persentase, pesan, dan estimasi sisa waktu sebuah job."""
    if not job_id:
        return

    with _lock:
        job = _jobs.get(job_id)

        if job is None:
            return

        job["percent"] = max(0, min(100, int(percent)))
        job["eta_seconds"] = eta_seconds
        job["updated_at"] = time.time()

        if message:
            job["message"] = message


def get_job(job_id):
    """Ambil snapshot status job dalam format yang aman dikirim sebagai JSON."""
    with _lock:
        job = _jobs.get(job_id)

        if job is None:
            return None

        return {
            "percent": job["percent"],
            "message": job["message"],
            "done": job["done"],
            "error": job["error"],
            "eta_seconds": job["eta_seconds"],
            "elapsed_seconds": int(time.time() - job.get("started_at", time.time())),
        }


def finish_job(job_id, error=None):
    """Tandai job selesai, baik berhasil maupun gagal dengan pesan error."""
    if not job_id:
        return

    with _lock:
        job = _jobs.get(job_id)

        if job is None:
            return

        job["done"] = True
        job["eta_seconds"] = None
        job["updated_at"] = time.time()

        if error is None:
            job["percent"] = 100
            job["message"] = "Selesai"
        else:
            job["error"] = error

    _log_job("finish", job_id, error=error, percent=job["percent"], message=job.get("message"))


def _purge_expired():
    """Hapus status job lama supaya memori tidak terus bertambah."""
    now = time.time()
    expired = [
        key
        for key, job in _jobs.items()
        if now - job.get("updated_at", now) > MAX_AGE_SECONDS
    ]

    for key in expired:
        del _jobs[key]

    if expired:
        from flask import current_app
        if current_app:
            current_app.logger.info("Job purge completed", extra={"context": {"purged": len(expired)}})
