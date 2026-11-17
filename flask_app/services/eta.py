import re
import time

_PAGE_PROGRESS_PATTERN = re.compile(r"Halaman (\d+) dari halaman (\d+)")


def format_interval(seconds):
    total_seconds = max(1, int(round(seconds)))

    if total_seconds < 60:
        return f"{total_seconds} detik"

    minutes, rest_seconds = divmod(total_seconds, 60)

    if minutes < 60:
        return f"{minutes} menit {rest_seconds:02d} detik"

    hours, rest_minutes = divmod(minutes, 60)
    return f"{hours} jam {rest_minutes:02d} menit {rest_seconds:02d} detik"


def _phase_key(message):
    if not message:
        return None

    if message.startswith("Halaman "):
        return "reading"

    if message.startswith("Menyusun "):
        return "normalize"

    if message.startswith("Menulis "):
        return "write"

    return None


ETA_MIN_SECONDS = 1.0
ETA_PHASE_WARMUP_SECONDS = 0.4
ETA_PHASE_MIN_SPAN = 2
ETA_DOWN_SMOOTHING = 0.35


def _raw_remaining(percent, elapsed, message, state):
    page_match = _PAGE_PROGRESS_PATTERN.match(message) if message else None

    if page_match:
        pages_read = int(page_match.group(1))
        total_pages = int(page_match.group(2))

        if pages_read > 0:
            return max(elapsed * (total_pages - pages_read) / pages_read, 0.0)

    # Laju per fase hanya bisa dipercaya pada fase tulis (fase terakhir).
    # Fase normalisasi berjalan jauh lebih cepat daripada fase tulis, sehingga
    # ekstrapolasinya membuat countdown jatuh ke 1 detik terlalu dini.
    if state.get("phase") != "write":
        return None

    phase_elapsed = elapsed - state.get("phase_started", 0.0)
    phase_span = percent - state.get("phase_percent", percent)

    if phase_elapsed >= ETA_PHASE_WARMUP_SECONDS and phase_span >= ETA_PHASE_MIN_SPAN:
        return max(phase_elapsed * (100 - percent) / phase_span, 0.0)

    return None


def _estimate_eta(percent, elapsed, message, state):
    if percent <= 0 or percent >= 100:
        state["eta"] = None
        state["eta_at"] = elapsed
        return None

    raw = _raw_remaining(percent, elapsed, message, state)
    previous_eta = state.get("eta")

    if previous_eta is None:
        if raw is None:
            state["eta"] = None
            state["eta_at"] = elapsed
            return None

        value = max(raw, ETA_MIN_SECONDS)
    else:
        previous_at = state.get("eta_at", 0.0)
        projected = max(previous_eta - (elapsed - previous_at), ETA_MIN_SECONDS)

        if raw is None or raw >= projected:
            value = projected
        else:
            value = projected + ETA_DOWN_SMOOTHING * (raw - projected)

    value = max(value, ETA_MIN_SECONDS)
    state["eta"] = value
    state["eta_at"] = elapsed
    return int(round(value))


class ProgressReporter:

    def __init__(self, on_update, on_phase=None, clock=time.monotonic):
        self._on_update = on_update
        self._on_phase = on_phase
        self._clock = clock
        self._started = clock()
        self._state = {"eta": None, "eta_at": 0.0, "phase": None, "phase_started": 0.0, "phase_percent": 0}

    def __call__(self, percent, message=None):
        elapsed = self._clock() - self._started
        phase = _phase_key(message)

        if phase != self._state["phase"]:
            self._state["phase"] = phase
            self._state["phase_started"] = elapsed
            self._state["phase_percent"] = percent

            if self._on_phase is not None:
                self._on_phase(phase, percent)

        eta = _estimate_eta(percent, elapsed, message, self._state)
        self._on_update(percent, message, eta, elapsed)
