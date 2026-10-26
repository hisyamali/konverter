"""Perhitungan progress dan ETA konversi yang dipakai bersama Flask dan Streamlit."""

import re
import time

_PAGE_PROGRESS_RE = re.compile(r"Halaman (\d+) dari halaman (\d+)")


def format_interval(seconds):
    """Ubah detik menjadi teks durasi ringkas berbahasa Indonesia."""
    total_seconds = max(1, int(round(seconds)))

    if total_seconds < 60:
        return f"{total_seconds} detik"

    minutes, rest_seconds = divmod(total_seconds, 60)

    if minutes < 60:
        return f"{minutes} menit {rest_seconds:02d} detik"

    hours, rest_minutes = divmod(minutes, 60)
    return f"{hours} jam {rest_minutes:02d} menit {rest_seconds:02d} detik"


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


class ProgressReporter:
    """Callback progress yang menghitung ETA dari pesan dan waktu berjalan.

    ``on_update(percent, message, eta_seconds, elapsed_seconds)`` dipanggil tiap
    update, dan ``on_phase(phase, percent)`` dipanggil saat fase berubah.
    """

    def __init__(self, on_update, on_phase=None, clock=time.monotonic):
        self._on_update = on_update
        self._on_phase = on_phase
        self._clock = clock
        self._started = clock()
        self._state = {"eta": None, "phase": None, "phase_started": 0.0, "phase_percent": 0}

    def __call__(self, percent, message=None):
        elapsed = self._clock() - self._started
        phase = _phase_key(message)

        if phase != self._state["phase"]:
            self._state["phase"] = phase
            self._state["phase_started"] = elapsed
            self._state["phase_percent"] = percent
            self._state["eta"] = None

            if self._on_phase is not None:
                self._on_phase(phase, percent)

        eta = _estimate_eta(percent, elapsed, message, self._state)
        self._state["eta"] = eta
        self._on_update(percent, message, eta, elapsed)
