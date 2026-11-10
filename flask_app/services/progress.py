import threading
import time

from flask import current_app

MAX_AGE_SECONDS = 600

_jobs_lock = threading.Lock()
_job_registry = {}


def _log_job(action, job_id, **context):
    if current_app:
        current_app.logger.info(f"Job {action}", extra={"context": {"job_id": job_id, **context}})


def create_job(job_id, message="Menyiapkan file"):
    with _jobs_lock:
        _purge_expired()
        _job_registry[job_id] = {
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
    if not job_id:
        return

    with _jobs_lock:
        job_status = _job_registry.get(job_id)

        if job_status is None:
            return

        job_status["percent"] = max(0, min(100, int(percent)))
        job_status["eta_seconds"] = eta_seconds
        job_status["updated_at"] = time.time()

        if message:
            job_status["message"] = message


def get_job(job_id):
    with _jobs_lock:
        job_status = _job_registry.get(job_id)

        if job_status is None:
            return None

        return {
            "percent": job_status["percent"],
            "message": job_status["message"],
            "done": job_status["done"],
            "error": job_status["error"],
            "eta_seconds": job_status["eta_seconds"],
            "elapsed_seconds": int(time.time() - job_status.get("started_at", time.time())),
        }


def finish_job(job_id, error=None):
    if not job_id:
        return

    with _jobs_lock:
        job_status = _job_registry.get(job_id)

        if job_status is None:
            return

        job_status["done"] = True
        job_status["eta_seconds"] = None
        job_status["updated_at"] = time.time()

        if error is None:
            job_status["percent"] = 100
            job_status["message"] = "Selesai"
        else:
            job_status["error"] = error

    _log_job("finish", job_id, error=error, percent=job_status["percent"], message=job_status.get("message"))


def _purge_expired():
    current_time = time.time()
    expired_job_ids = [
        job_id
        for job_id, job_status in _job_registry.items()
        if current_time - job_status.get("updated_at", current_time) > MAX_AGE_SECONDS
    ]

    for job_id in expired_job_ids:
        del _job_registry[job_id]

    if expired_job_ids:
        from flask import current_app
        if current_app:
            current_app.logger.info("Job purge completed", extra={"context": {"purged": len(expired_job_ids)}})
