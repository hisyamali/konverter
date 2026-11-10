import os
import threading
import time
from contextlib import contextmanager

MAX_CONCURRENT = int(os.environ.get("KONVERTER_MAX_CONCURRENT", "4") or "4")
MAX_WAIT_SECONDS = int(os.environ.get("KONVERTER_QUEUE_WAIT_SECONDS", "900") or "900")
POLL_SECONDS = 0.5

_state_lock = threading.Lock()
_conversion_semaphore = None
_waiting_count = 0
_running_count = 0


class QueueTimeout(Exception):
    pass


def _get_conversion_semaphore():
    global _conversion_semaphore

    with _state_lock:
        if _conversion_semaphore is None:
            _conversion_semaphore = threading.BoundedSemaphore(max(1, MAX_CONCURRENT))

        return _conversion_semaphore


def active_conversions():
    with _state_lock:
        return _running_count


def waiting_conversions():
    with _state_lock:
        return _waiting_count


def _workers_for_active_jobs(total_workers, active_count):
    if active_count <= 1:
        return total_workers

    return max(1, total_workers // active_count)


def worker_budget(total_workers):
    active_count = active_conversions()
    return _workers_for_active_jobs(total_workers, max(active_count, 1))


@contextmanager
def conversion_slot(on_wait=None):
    conversion_semaphore = _get_conversion_semaphore()
    global _waiting_count, _running_count

    if conversion_semaphore.acquire(blocking=False):
        with _state_lock:
            _running_count += 1

        try:
            yield
        finally:
            with _state_lock:
                _running_count -= 1
            conversion_semaphore.release()

        return

    with _state_lock:
        _waiting_count += 1

    started_at = time.monotonic()

    try:
        while True:
            if conversion_semaphore.acquire(timeout=POLL_SECONDS):
                break

            waited_seconds = int(time.monotonic() - started_at)

            if MAX_WAIT_SECONDS and waited_seconds >= MAX_WAIT_SECONDS:
                raise QueueTimeout("Server sedang sibuk. Coba lagi beberapa saat lagi.")

            if on_wait is not None:
                with _state_lock:
                    queue_position = _waiting_count
                on_wait(waited_seconds, queue_position)
    finally:
        with _state_lock:
            _waiting_count -= 1

    with _state_lock:
        _running_count += 1

    try:
        yield
    finally:
        with _state_lock:
            _running_count -= 1
        conversion_semaphore.release()
