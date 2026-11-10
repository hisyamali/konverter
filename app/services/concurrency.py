"""Pembatas jumlah konversi bersamaan agar mesin tidak kelebihan beban.

Setiap permintaan konversi mengambil satu slot. Bila semua slot terisi,
permintaan berikutnya menunggu antrean sambil melaporkan posisinya ke UI,
lalu ditolak dengan pesan "server sibuk" bila menunggu terlalu lama.
"""

import os
import threading
import time
from contextlib import contextmanager

MAX_CONCURRENT = int(os.environ.get("KONVERTER_MAX_CONCURRENT", "4") or "4")
MAX_WAIT_SECONDS = int(os.environ.get("KONVERTER_QUEUE_WAIT_SECONDS", "900") or "900")
POLL_SECONDS = 0.5

_lock = threading.Lock()
_semaphore = None
_waiting = 0
_running = 0


class QueueTimeout(Exception):
    """Permintaan menunggu slot terlalu lama."""


def _get_semaphore():
    """Buat semaphore slot sekali saja sesuai konfigurasi."""
    global _semaphore

    with _lock:
        if _semaphore is None:
            _semaphore = threading.BoundedSemaphore(max(1, MAX_CONCURRENT))

        return _semaphore


def active_conversions():
    """Jumlah konversi yang sedang benar-benar berjalan (memakai slot)."""
    with _lock:
        return _running


def waiting_conversions():
    """Jumlah permintaan yang sedang menunggu slot."""
    with _lock:
        return _waiting


def _workers_for_active_jobs(total_workers, active):
    """Bagi anggaran worker ke seluruh konversi aktif agar totalnya mendekati core."""
    if active <= 1:
        return total_workers

    return max(1, total_workers // active)


def worker_budget(total_workers):
    """Hitung batas worker untuk konversi ini berdasarkan konversi lain yang aktif."""
    active = active_conversions()
    return _workers_for_active_jobs(total_workers, max(active, 1))


@contextmanager
def conversion_slot(on_wait=None):
    """Ambil slot konversi; panggil ``on_wait(menunggu, posisi)`` selama mengantre."""
    semaphore = _get_semaphore()
    global _waiting, _running

    if semaphore.acquire(blocking=False):
        with _lock:
            _running += 1

        try:
            yield
        finally:
            with _lock:
                _running -= 1
            semaphore.release()

        return

    with _lock:
        _waiting += 1

    started = time.monotonic()

    try:
        while True:
            if semaphore.acquire(timeout=POLL_SECONDS):
                break

            waited = int(time.monotonic() - started)

            if MAX_WAIT_SECONDS and waited >= MAX_WAIT_SECONDS:
                raise QueueTimeout("Server sedang sibuk. Coba lagi beberapa saat lagi.")

            if on_wait is not None:
                with _lock:
                    position = _waiting
                on_wait(waited, position)
    finally:
        with _lock:
            _waiting -= 1

    with _lock:
        _running += 1

    try:
        yield
    finally:
        with _lock:
            _running -= 1
        semaphore.release()
