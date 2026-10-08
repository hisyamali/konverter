import threading
import time

MAX_AGE_SECONDS = 600

_lock = threading.Lock()
_jobs = {}


def create_job(job_id, message="Menyiapkan file"):
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

    return job_id


def update_job(job_id, percent, message=None, eta_seconds=None):
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


def _purge_expired():
    now = time.time()
    expired = [
        key
        for key, job in _jobs.items()
        if now - job.get("updated_at", now) > MAX_AGE_SECONDS
    ]

    for key in expired:
        del _jobs[key]