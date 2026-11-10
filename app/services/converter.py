import csv
import math
import os
import re
import time
import weakref
from concurrent.futures import ProcessPoolExecutor, TimeoutError, as_completed
from concurrent.futures.process import BrokenProcessPool
from decimal import Decimal, InvalidOperation

import pandas
import pdfplumber
from flask import current_app, has_app_context
from openpyxl import Workbook

from app.services import concurrency

try:
    import pymupdf
except ImportError:
    pymupdf = None

try:
    import psutil
except ImportError:
    psutil = None


_FAST_WORDS_ENABLED = os.environ.get("KONVERTER_PDF_WORDS", "1") not in ("0", "false", "False")
FAST_WORDS_AVAILABLE = pymupdf is not None and _FAST_WORDS_ENABLED


def _get_memory_usage_mb():
    if psutil is None:
        return None
    try:
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        return round(memory_info.rss / 1024 / 1024, 1)
    except Exception:
        return None


def _get_system_memory_percent():
    if psutil is None:
        return None
    try:
        return psutil.virtual_memory().percent
    except Exception:
        return None


def _log_milestone(milestone, **context):
    if has_app_context():
        current_app.logger.info(f"Conversion {milestone}", extra={"context": context})


def _log_warning(message, **context):
    if has_app_context():
        current_app.logger.warning(message, extra={"context": context})


def _log_error(error, **context):
    if has_app_context():
        current_app.logger.error(
            "Conversion error",
            extra={"context": {"error": str(error), "error_type": type(error).__name__, **context}},
            exc_info=True,
        )


def _physical_core_count():
    try:
        if os.name != "nt":
            return None

        import ctypes

        class SystemLogicalProcessorInformation(ctypes.Structure):
            _fields_ = [
                ("ProcessorMask", ctypes.c_size_t),
                ("Relationship", ctypes.c_int),
                ("Reserved", ctypes.c_ubyte * 16),
            ]

        get_info = ctypes.windll.kernel32.GetLogicalProcessorInformation
        line_length = ctypes.c_ulong(0)
        get_info(None, ctypes.byref(line_length))

        if line_length.value <= 0:
            return None

        count = line_length.value // ctypes.sizeof(SystemLogicalProcessorInformation)
        buffer = (SystemLogicalProcessorInformation * count)()

        if not get_info(buffer, ctypes.byref(line_length)):
            return None

        cores = sum(1 for item in buffer if item.Relationship == 0)
        return cores or None
    except Exception:
        return None


def _physical_cores():
    detected_core_count = _physical_core_count()

    if detected_core_count:
        return detected_core_count

    logical_cpu_count = os.cpu_count() or 1
    return max(1, logical_cpu_count // 2)


PARALLEL_MIN_PAGES = int(os.environ.get("KONVERTER_MIN_PARALLEL_PAGES", "60"))
PARALLEL_MAX_WORKERS_CAP = int(os.environ.get("KONVERTER_MAX_WORKERS_CAP", "8"))
PARALLEL_CHUNK_PAGES = int(os.environ.get("KONVERTER_CHUNK_PAGES", "10"))
PARALLEL_MAX_TASKS_PER_CHILD = int(os.environ.get("KONVERTER_TASKS_PER_CHILD", "32"))
TEXT_PARALLEL_MIN_PAGES = int(
    os.environ.get("KONVERTER_TEXT_MIN_PARALLEL_PAGES", "1000" if FAST_WORDS_AVAILABLE else str(PARALLEL_MIN_PAGES))
)
PARALLEL_STALL_REPORT_SECONDS = 45
PARALLEL_DEGRADE_SECONDS = 90
PARALLEL_MEMORY_GB_PER_WORKER = float(os.environ.get("KONVERTER_MEM_GB_PER_WORKER", "0.15"))
PARALLEL_MEMORY_RESERVE_GB = float(os.environ.get("KONVERTER_MEM_RESERVE_GB", "0.4"))
PARALLEL_MEMORY_FLOOR_GB = float(os.environ.get("KONVERTER_MEM_FLOOR_GB", "0.35"))
POLITE_MODE = os.environ.get("KONVERTER_POLITE", "1") not in ("0", "false", "False")

PROGRESS_PAGE_STEP = int(os.environ.get("KONVERTER_PROGRESS_PAGE_STEP", "5"))

_workers_override = int(os.environ.get("KONVERTER_MAX_WORKERS", "0") or "0")
PARALLEL_MAX_WORKERS = (
    _workers_override
    if _workers_override > 0
    else min(_physical_cores(), PARALLEL_MAX_WORKERS_CAP)
)

_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

PDF_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 5,
    "join_tolerance": 5,
    "edge_min_length": 5,
}

TEXT_DATE_X_MAX = 50
TEXT_DESC_X_MIN = 90
TEXT_DESC_X_MAX = 270
TEXT_DEBET_X = (270, 340)
TEXT_KREDIT_X = (340, 420)
TEXT_SALDO_X = (420, 510)
TEXT_REF_X = (510, 590)
TEXT_HEADER = ["Tanggal", "Keterangan", "Debet", "Kredit", "Saldo", "No. Referensi"]
TEXT_ROW_Y_TOLERANCE = 3

_WORKER_PDF = None


def convert_csv_to_excel(input_path, output_path, progress=None):
    memory_start_mb = _get_memory_usage_mb()
    _log_milestone("reading", input_path=input_path, memory_mb=memory_start_mb)
    _report_progress(progress, 12, "Membaca file CSV")
    dataframe = _read_csv(input_path)

    if dataframe.empty:
        raise ValueError("File CSV tidak memiliki data.")

    _log_milestone("normalize", rows=len(dataframe), columns=list(dataframe.columns))
    _report_progress(progress, 70, "Menyusun kolom data")

    _normalize_nominal_values(dataframe)

    _log_milestone("write", rows=len(dataframe))
    _report_progress(progress, 88, "Menulis file Excel")
    _write_excel(dataframe, output_path)

    memory_end_mb = _get_memory_usage_mb()
    _log_milestone("done", output_path=output_path, memory_mb=memory_end_mb, memory_delta_mb=round(memory_end_mb - memory_start_mb, 1) if memory_start_mb and memory_end_mb else None)
    _report_progress(progress, 100, "Selesai")


def _write_excel(dataframe, output_path, sheet_name="Sheet1", include_header=True):
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet(sheet_name)

    if include_header:
        sheet.append(list(dataframe.columns))

    for row in dataframe.itertuples(index=False, name=None):
        sheet.append(row)

    workbook.save(output_path)


def convert_pdf_to_excel(input_path, output_path, progress=None, total_pages=None):
    memory_start_mb = _get_memory_usage_mb()
    system_memory_start_percent = _get_system_memory_percent()

    strategy = _detect_pdf_strategy(input_path)
    _log_milestone("reading", input_path=input_path, total_pages=total_pages, memory_mb=memory_start_mb, system_memory_percent=system_memory_start_percent, strategy=strategy)

    if total_pages is None:
        _report_progress(progress, 6, "Membuka file PDF")

    if strategy == "lines":
        builder = _RowBuilder()
        _collect_pdf_tables(input_path, progress, total_pages, builder.add_tables)
        rows = builder.rows
    else:
        rows = []
        _collect_text_rows(input_path, progress, total_pages, rows.extend)
        if rows:
            rows = [list(TEXT_HEADER)] + rows

    if not rows:
        raise ValueError("Tidak ada tabel yang bisa dibaca dari PDF.")

    memory_after_read_mb = _get_memory_usage_mb()
    _log_milestone("normalize", rows=len(rows), columns=None, memory_mb=memory_after_read_mb, memory_delta_mb=round(memory_after_read_mb - memory_start_mb, 1) if memory_start_mb and memory_after_read_mb else None, strategy=strategy)
    _report_progress(progress, 86, "Menyusun kolom data")

    _normalize_table(rows)
    dataframe = pandas.DataFrame(rows)
    _normalize_nominal_values(dataframe)

    _log_milestone("write", rows=len(dataframe), columns=list(dataframe.columns))
    _report_progress(progress, 93, "Menulis file Excel")
    _write_excel(dataframe, output_path, sheet_name="Data", include_header=False)

    memory_end_mb = _get_memory_usage_mb()
    system_memory_end_percent = _get_system_memory_percent()
    _log_milestone("done", output_path=output_path, rows=len(dataframe), memory_mb=memory_end_mb, memory_delta_mb=round(memory_end_mb - memory_start_mb, 1) if memory_start_mb and memory_end_mb else None, system_memory_percent=system_memory_end_percent, strategy=strategy)
    _report_progress(progress, 100, "Selesai")


def _collect_pdf_tables(input_path, progress, total_pages, on_tables):
    if total_pages is None:
        total_pages = _count_pages(input_path)

    worker_count, use_pool = _plan_read(total_pages)

    _report_progress(progress, 8, f"Halaman 0 dari halaman {total_pages}")

    if not should_use_pool:
        _read_pages_sequential(input_path, total_pages, progress, on_tables)
        return

    _read_chunks_parallel(input_path, _build_chunks(total_pages), worker_count, total_pages, progress, on_tables)


def _count_pages(input_path):
    document = _open_text_document(input_path)

    try:
        return _document_page_count(document)
    finally:
        document.close()


def _open_text_document(input_path):
    if FAST_WORDS_AVAILABLE:
        return pymupdf.open(str(input_path))

    return pdfplumber.open(str(input_path))


def _document_page_count(document):
    page_count = getattr(document, "page_count", None)

    if isinstance(page_count, int):
        return page_count

    page_list = getattr(document, "pages", None)

    if isinstance(page_list, list):
        return len(page_list)

    return len(document)


def _iter_pages(document, start=0):
    page_list = getattr(document, "pages", None)

    if isinstance(page_list, list):
        return page_list[start:]

    return (document[index] for index in range(start, len(document)))


def _page_words(page):
    if hasattr(page, "extract_words"):
        return page.extract_words()

    return [
        {"text": word[4], "x0": word[0], "top": word[1]}
        for word in page.get_text("words")
    ]


def _worker_count(total_pages, minimum=PARALLEL_MIN_PAGES):
    if total_pages <= minimum:
        return 0

    logical_cpu_count = os.cpu_count() or 1
    worker_count = min(logical_cpu_count, PARALLEL_MAX_WORKERS)
    available_memory_gb = _available_memory_gb()

    if available_memory_gb is not None:
        workers_by_memory = int((available_memory_gb - PARALLEL_MEMORY_RESERVE_GB) / PARALLEL_MEMORY_GB_PER_WORKER)
        worker_count = min(worker_count, max(workers_by_memory, 1))

    return max(1, worker_count)


def _spawn_allowed():
    available_memory_gb = _available_memory_gb()

    if available_memory_gb is None:
        return True

    return available_memory_gb >= PARALLEL_MEMORY_RESERVE_GB + PARALLEL_MEMORY_GB_PER_WORKER


def _plan_read(total_pages, minimum=PARALLEL_MIN_PAGES):
    if not _spawn_allowed():
        return 1, False

    base_worker_count = _worker_count(total_pages, minimum)
    other_conversions_running = concurrency.active_conversions() > 1

    if base_worker_count <= 1:
        return (1, True) if other_conversions_running else (1, False)

    return max(1, concurrency.worker_budget(base_worker_count)), True


def _memory_pressure():
    available_memory_gb = _available_memory_gb()
    return available_memory_gb is not None and available_memory_gb < PARALLEL_MEMORY_FLOOR_GB


def _available_memory_gb():
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_phys", ctypes.c_ulonglong),
                    ("avail_phys", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("avail_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_ext_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.length = ctypes.sizeof(MemoryStatusEx)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.avail_phys / (1024 ** 3)

        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", "r", encoding="utf-8") as memory_info_file:
                for line in memory_info_file:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) / (1024 ** 2)

        return None
    except Exception:
        return None


def _build_chunks(total_pages):
    return [
        (start, min(start + PARALLEL_CHUNK_PAGES, total_pages))
        for start in range(0, total_pages, PARALLEL_CHUNK_PAGES)
    ]


def _read_pages_sequential(input_path, total_pages, progress, on_tables, start=0):
    pages_read_count = start

    with pdfplumber.open(input_path) as pdf:
        for page in pdf.pages[start:]:
            on_tables(_page_tables(page))
            pages_read_count += 1
            _report_progress(progress, _percent_from_pages(pages_read_count, total_pages), _status_message(pages_read_count, total_pages))


def _read_chunks_parallel(input_path, chunks, worker_count, total_pages, progress, on_tables):
    chunk_end = dict(chunks)
    buffered_chunks = {}
    next_expected_page = 0
    pages_read_count = 0
    last_success_time = time.monotonic()
    is_degraded = False
    degrade_reason = None

    process_executor = ProcessPoolExecutor(
        max_workers=worker_count,
        initializer=_init_pdf_worker,
        initargs=(str(input_path),),
        max_tasks_per_child=PARALLEL_MAX_TASKS_PER_CHILD,
    )
    submitted_futures = {process_executor.submit(_extract_chunk_task, chunk): chunk for chunk in chunks}
    pending_futures = set(submitted_futures)

    try:
        while pending_futures and not is_degraded:
            try:
                for future in as_completed(pending_futures, timeout=PARALLEL_STALL_REPORT_SECONDS):
                    chunk_start, chunk_tables = future.result()
                    buffered_chunks[chunk_start] = chunk_tables
                    chunk_start, chunk_stop = submitted_futures[future]
                    pages_read_count += chunk_stop - chunk_start
                    pending_futures.remove(future)
                    last_success_time = time.monotonic()

                    while next_expected_page in buffered_chunks:
                        on_tables(buffered_chunks.pop(next_expected_page))
                        next_expected_page = chunk_end[next_expected_page]

                    _report_progress(progress, _percent_from_pages(pages_read_count, total_pages), _status_message(pages_read_count, total_pages))

                    if _memory_pressure():
                        is_degraded = True
                        degrade_reason = "memori menipis"
                        break
            except TimeoutError:
                stalled_seconds = int(time.monotonic() - last_success_time)
                _report_progress(
                    progress,
                    _percent_from_pages(pages_read_count, total_pages),
                    f"{_status_message(pages_read_count, total_pages)} — pembacaan melambat (±{stalled_seconds} detik), tetap berjalan",
                )

                if stalled_seconds >= PARALLEL_DEGRADE_SECONDS:
                    is_degraded = True
                    degrade_reason = "pembacaan melambat"
    except BrokenProcessPool as error:
        _abort_executor(process_executor, pending_futures)
        _log_error(error, stage="parallel_read", total_pages=total_pages, pages_read=pages_read_count)
        raise RuntimeError("Proses pembacaan PDF terhenti. Silakan coba lagi.")
    except Exception as error:
        _abort_executor(process_executor, pending_futures)
        _log_error(error, stage="parallel_read", total_pages=total_pages, pages_read=pages_read_count)
        raise
    else:
        if is_degraded:
            _abort_executor(process_executor, pending_futures)
        else:
            process_executor.shutdown(wait=True)

    if is_degraded:
        if degrade_reason == "memori menipis":
            message = f"{_status_message(next_expected_page, total_pages)} — memori menipis, beralih ke mode hemat (satu-per-satu)"
        else:
            message = f"{_status_message(next_expected_page, total_pages)} — beralih ke mode satu-per-satu agar pasti selesai"

        _log_warning("Conversion degraded", reason=degrade_reason, next_expected=next_expected_page, total_pages=total_pages)
        _report_progress(progress, _percent_from_pages(next_expected_page, total_pages), message)
        _read_pages_sequential(input_path, total_pages, progress, on_tables, start=next_expected_page)


def _abort_executor(process_executor, pending_futures):
    for future in pending_futures:
        future.cancel()

    try:
        process_executor.shutdown(wait=False, cancel_futures=True)
    except ValueError:
        pass


def _init_pdf_worker(pdf_path):
    _apply_polite_priority()
    global _WORKER_PDF
    _WORKER_PDF = pdfplumber.open(pdf_path)


def _apply_polite_priority():
    if not POLITE_MODE:
        return

    try:
        if os.name == "nt":
            import ctypes

            process_handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetPriorityClass(process_handle, _BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(5)
    except Exception:
        pass


def _extract_chunk_task(chunk):
    start, end = chunk
    return start, _extract_chunk(_WORKER_PDF, start, end)


def _detect_pdf_strategy(input_path):
    try:
        with pdfplumber.open(input_path) as pdf:
            if not pdf.pages:
                return "lines"
            preview_tables = pdf.pages[0].extract_tables(PDF_TABLE_SETTINGS)
    except Exception:
        return "lines"

    data_rows = 0
    if preview_tables:
        for table in preview_tables:
            if table:
                data_rows += max(len(table) - 1, 0)

    return "lines" if data_rows >= 1 else "text"


def _extract_text_page(page):
    words = _page_words(page)
    words.sort(key=lambda w: (w["top"], w["x0"]))

    word_clusters = []
    for word in words:
        if word_clusters and abs(word_clusters[-1]["y"] - word["top"]) <= TEXT_ROW_Y_TOLERANCE:
            word_clusters[-1]["words"].append(word)
        else:
            word_clusters.append({"y": word["top"], "words": [word]})

    date_pattern = re.compile(r"^\d{2}-\d{2}-\d{4}$")
    rows = []
    current = None

    def _amount_in(tokens, bounds):
        low, high = bounds
        return " ".join(word["text"] for word in tokens if low <= word["x0"] < high)

    for cluster in word_clusters:
        tokens = sorted(cluster["words"], key=lambda word: word["x0"])
        if not tokens:
            continue

        first_token = tokens[0]
        if date_pattern.match(first_token["text"]) and first_token["x0"] < TEXT_DATE_X_MAX:
            if current is not None:
                rows.append(_finalize_text_transaction(current))
            current = {
                "date": first_token["text"],
                "debet": _amount_in(tokens, TEXT_DEBET_X),
                "kredit": _amount_in(tokens, TEXT_KREDIT_X),
                "saldo": _amount_in(tokens, TEXT_SALDO_X),
                "ref": _amount_in(tokens, TEXT_REF_X),
                "desc": [],
            }
            continue

        if current is None:
            continue

        line_text = " ".join(word["text"] for word in tokens)
        if re.match(r"Page:", line_text):
            continue

        description_words = [word["text"] for word in tokens if TEXT_DESC_X_MIN <= word["x0"] < TEXT_DESC_X_MAX]
        if description_words:
            current["desc"].append(" ".join(description_words))

    if current is not None:
        rows.append(_finalize_text_transaction(current))

    return rows


def _finalize_text_transaction(transaction):
    description = "\n".join(transaction["desc"]) if transaction["desc"] else ""
    return [
        transaction["date"],
        description,
        transaction["debet"],
        transaction["kredit"],
        transaction["saldo"],
        transaction["ref"],
    ]


def _extract_text_chunk_task(chunk):
    start, end = chunk
    rows = []

    for page in _iter_text_pages(_WORKER_PDF, start, end):
        rows.extend(_extract_text_page(page))
    return start, rows


def _iter_text_pages(document, start, end):
    page_list = getattr(document, "pages", None)

    if isinstance(page_list, list):
        return page_list[start:end]

    return (document[index] for index in range(start, min(end, len(document))))


def _init_text_worker(pdf_path):
    _apply_polite_priority()
    global _WORKER_PDF
    _WORKER_PDF = _open_text_document(pdf_path)


def _read_text_pages_sequential(input_path, total_pages, progress, on_rows, start=0):
    pages_read_count = start
    document = _open_text_document(input_path)

    try:
        for page in _iter_pages(document, start):
            on_rows(_extract_text_page(page))
            pages_read_count += 1
            _report_progress(progress, _percent_from_pages(pages_read_count, total_pages), _status_message(pages_read_count, total_pages))
    finally:
        document.close()


def _read_text_chunks_parallel(input_path, chunks, worker_count, total_pages, progress, on_rows):
    chunk_end = dict(chunks)
    buffered_chunks = {}
    next_expected_page = 0
    pages_read_count = 0
    last_success_time = time.monotonic()
    is_degraded = False
    degrade_reason = None

    process_executor = ProcessPoolExecutor(
        max_workers=worker_count,
        initializer=_init_text_worker,
        initargs=(str(input_path),),
        max_tasks_per_child=PARALLEL_MAX_TASKS_PER_CHILD,
    )
    submitted_futures = {process_executor.submit(_extract_text_chunk_task, chunk): chunk for chunk in chunks}
    pending_futures = set(submitted_futures)

    try:
        while pending_futures and not is_degraded:
            try:
                for future in as_completed(pending_futures, timeout=PARALLEL_STALL_REPORT_SECONDS):
                    chunk_start, chunk_rows = future.result()
                    buffered_chunks[chunk_start] = chunk_rows
                    chunk_start_key, chunk_stop = submitted_futures[future]
                    pages_read_count += chunk_stop - chunk_start_key
                    pending_futures.remove(future)
                    last_success_time = time.monotonic()

                    while next_expected_page in buffered_chunks:
                        on_rows(buffered_chunks.pop(next_expected_page))
                        next_expected_page = chunk_end[next_expected_page]

                    _report_progress(progress, _percent_from_pages(pages_read_count, total_pages), _status_message(pages_read_count, total_pages))

                    if _memory_pressure():
                        is_degraded = True
                        degrade_reason = "memori menipis"
                        break
            except TimeoutError:
                stalled_seconds = int(time.monotonic() - last_success_time)
                _report_progress(
                    progress,
                    _percent_from_pages(pages_read_count, total_pages),
                    f"{_status_message(pages_read_count, total_pages)} — pembacaan melambat (±{stalled_seconds} detik), tetap berjalan",
                )

                if stalled_seconds >= PARALLEL_DEGRADE_SECONDS:
                    is_degraded = True
                    degrade_reason = "pembacaan melambat"
    except BrokenProcessPool:
        _abort_executor(process_executor, pending_futures)
        raise RuntimeError("Proses pembacaan PDF terhenti. Silakan coba lagi.")
    except Exception as error:
        _abort_executor(process_executor, pending_futures)
        _log_error(error, stage="parallel_text_read", total_pages=total_pages, pages_read=pages_read_count)
        raise
    else:
        if is_degraded:
            _abort_executor(process_executor, pending_futures)
        else:
            process_executor.shutdown(wait=True)

    if is_degraded:
        if degrade_reason == "memori menipis":
            message = f"{_status_message(next_expected_page, total_pages)} — memori menipis, beralih ke mode hemat (satu-per-satu)"
        else:
            message = f"{_status_message(next_expected_page, total_pages)} — beralih ke mode satu-per-satu agar pasti selesai"

        _log_warning("Conversion degraded", reason=degrade_reason, next_expected=next_expected_page, total_pages=total_pages)
        _report_progress(progress, _percent_from_pages(next_expected_page, total_pages), message)
        _read_text_pages_sequential(input_path, total_pages, progress, on_rows, start=next_expected_page)


def _collect_text_rows(input_path, progress, total_pages, on_rows):
    if total_pages is None:
        total_pages = _count_pages(input_path)

    worker_count, use_pool = _plan_read(total_pages, TEXT_PARALLEL_MIN_PAGES)

    _report_progress(progress, 8, f"Halaman 0 dari halaman {total_pages}")

    if not should_use_pool:
        _read_text_pages_sequential(input_path, total_pages, progress, on_rows)
        return

    _read_text_chunks_parallel(input_path, _build_chunks(total_pages), worker_count, total_pages, progress, on_rows)


def _page_tables(page):
    tables = []

    try:
        for table in page.extract_tables(PDF_TABLE_SETTINGS):
            cleaned_table = _clean_table(table)

            if cleaned_table:
                tables.append(cleaned_table)
    finally:
        page.flush_cache()

    return tables


def _extract_chunk(pdf, start, end):
    tables = []

    for page in pdf.pages[start:end]:
        tables.extend(_page_tables(page))

    return tables


def _percent_from_pages(pages_read_count, total_pages):
    return 8 + int(76 * pages_read_count / max(total_pages, 1))


def _status_message(pages_read_count, total_pages):
    return f"Halaman {pages_read_count} dari halaman {total_pages}"


_PAGE_STATUS_PATTERN = re.compile(r"^Halaman (\d+) dari halaman (\d+)$")
_progress_state = weakref.WeakKeyDictionary()


def _should_report_pages(progress, page_list):
    try:
        last_reported_pages = _progress_state.get(progress)
    except TypeError:
        return True

    if last_reported_pages is not None and page_list - last_reported_pages < PROGRESS_PAGE_STEP:
        return False

    try:
        _progress_state[progress] = page_list
    except TypeError:
        pass

    return True


def _report_progress(progress, percent, message):
    if progress is None:
        return

    matched_pattern = _PAGE_STATUS_PATTERN.match(message) if message else None

    if matched_pattern and not _should_report_pages(progress, int(matched_pattern.group(1))):
        return

    progress(percent, message)


PREVIEW_SAMPLE_ROWS = 10
PREVIEW_PDF_TABLE_ROWS = 5


def preview_csv_file(path):
    dataframe = _read_csv(path)

    if dataframe.empty:
        raise ValueError("File CSV tidak memiliki data.")

    headers = [str(column) for column in dataframe.columns]
    sample = dataframe.head(PREVIEW_SAMPLE_ROWS)

    return {
        "row_count": len(dataframe),
        "column_count": len(headers),
        "headers": headers,
        "sample_rows": [_json_rows(row) for row in sample.itertuples(index=False, name=None)],
    }


PREVIEW_TIMED_PAGES = 5


def preview_pdf_file(path):
    strategy = _detect_pdf_strategy(path)
    lines_mode = strategy == "lines"
    document = pdfplumber.open(path) if lines_mode else _open_text_document(path)

    try:
        page_count = _document_page_count(document)

        if page_count == 0:
            raise ValueError("Tidak ada halaman yang bisa dibaca dari PDF.")

        first_page = document.pages[0] if lines_mode else document[0]

        sample_rows = []
        if lines_mode:
            preview_tables = first_page.extract_tables(PDF_TABLE_SETTINGS)
            if preview_tables:
                for row in preview_tables[0][:PREVIEW_PDF_TABLE_ROWS]:
                    sample_rows.append(
                        [(cell or "").strip() if cell else "" for cell in row]
                    )
        else:
            for row in _extract_text_page(first_page)[:PREVIEW_PDF_TABLE_ROWS]:
                sample_rows.append([str(cell) for cell in row])

        if page_count > 1:
            page_count_timed = min(PREVIEW_TIMED_PAGES, page_count)
            start = min(page_count // 2, page_count - page_count_timed)
            timed_start = time.perf_counter()
            timed_pages = 0

            for index in range(start, start + page_count_timed):
                try:
                    if lines_mode:
                        document.pages[index].extract_tables(PDF_TABLE_SETTINGS)
                    else:
                        _page_words(document[index])
                except Exception:
                    continue

                timed_pages += 1

            elapsed_sample = time.perf_counter() - timed_start
            per_page_seconds = elapsed_sample / max(timed_pages, 1)
        else:
            timed_pages = 0
            per_page_seconds = 0.0
    finally:
        document.close()

    return {
        "page_count": page_count,
        "sample_rows": sample_rows,
        "estimated_seconds": int(round(per_page_seconds * page_count)) if timed_pages else 0,
    }


def _json_rows(row):
    return [_json_value(value) for value in row]


def _json_value(value):
    if value is None:
        return None

    if hasattr(value, "item"):
        value = value.item()

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    return str(value)


def _read_csv(input_path):
    table = _read_transaction_csv(input_path)
    if table is not None:
        return table

    return _read_generic_csv(input_path)


def _read_generic_csv(input_path):
    if not _has_content(input_path):
        return pandas.DataFrame()

    delimiter = _sniff_delimiter(input_path)

    if delimiter is not None:
        try:
            return pandas.read_csv(input_path, sep=delimiter, engine="c")
        except Exception:
            try:
                return pandas.read_csv(input_path, sep=delimiter, engine="c", encoding="latin-1")
            except Exception:
                pass

    try:
        return pandas.read_csv(input_path, sep=None, engine="python")
    except UnicodeDecodeError:
        return pandas.read_csv(input_path, sep=None, engine="python", encoding="latin-1")


def _has_content(input_path):
    whitespace = b"\x00\r\n\t "
    try:
        with open(input_path, "rb") as file_handle:
            while True:
                chunk = file_handle.read(65536)

                if not chunk:
                    return False

                if any(byte not in whitespace for byte in chunk):
                    return True
    except OSError:
        return True


def _sniff_delimiter(input_path):
    try:
        with open(input_path, "rb") as file_handle:
            sample = file_handle.read(8192)
    except OSError:
        return None

    text = None

    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = sample.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        text = sample.decode("latin-1", errors="replace")

    comma_count = 0
    semicolon_count = 0

    for line in text.splitlines():
        stripped = line.strip()

        if not stripped:
            continue

        comma_count += _count_unquoted(stripped, ",")
        semicolon_count += _count_unquoted(stripped, ";")

    if comma_count == 0 and semicolon_count == 0:
        return None

    if comma_count == semicolon_count:
        return None

    return "," if comma_count > semicolon_count else ";"


def _count_unquoted(line, character):
    count = 0
    in_quotes = False
    index = 0
    line_length = len(line)

    while index < line_length:
        current = line[index]

        if current == '"':
            if in_quotes and index + 1 < line_length and line[index + 1] == '"':
                index += 2
                continue

            in_quotes = not in_quotes
        elif current == character and not in_quotes:
            count += 1

        index += 1

    return count


def _read_transaction_csv(input_path):
    lines = _read_text_lines(input_path)

    if not lines:
        return None

    header_index, delimiter, columns = _find_transaction_header(lines)

    if header_index is None:
        return None

    rows = []
    column_count = len(columns)

    for line in lines[header_index + 1:]:
        if not line.strip():
            continue

        row = next(csv.reader([line], delimiter=delimiter))
        row = _fit_csv_row(row, column_count)

        if any(cell.strip() for cell in row):
            rows.append(row)

    return pandas.DataFrame(rows, columns=columns)


def _read_text_lines(input_path):
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with open(input_path, encoding=encoding, newline="") as file_handle:
                return file_handle.read().splitlines()
        except UnicodeDecodeError:
            continue

    return []


def _find_transaction_header(lines):
    for index, line in enumerate(lines):
        for delimiter in (",", ";"):
            columns = next(csv.reader([line], delimiter=delimiter))
            normalized_columns = _normalize_row(columns)

            if "tanggal" in normalized_columns and "keterangan" in normalized_columns:
                return index, delimiter, columns

    return None, None, None


def _fit_csv_row(row, column_count):
    row = [cell.strip() for cell in row]

    if len(row) == column_count:
        return row

    if len(row) > column_count and column_count >= 3:
        fixed_tail_count = column_count - 2
        description = " ".join(cell for cell in row[1:-fixed_tail_count] if cell).strip()
        return [row[0], description, *row[-fixed_tail_count:]]

    return row + [""] * (column_count - len(row))


def _clean_table(table):
    cleaned_rows = []

    for row in table:
        if row is None:
            continue

        cleaned_row = [(cell or "").strip() for cell in row]
        if any(cleaned_row):
            cleaned_rows.append(cleaned_row)

    return cleaned_rows


class _RowBuilder:

    def __init__(self):
        self.header = None
        self.rows = []

    def add_tables(self, tables):
        for table in tables:
            if not table:
                continue

            if self.header is None:
                self.header = table[0]
                self.rows.extend(table)
            elif _same_row(table[0], self.header):
                self.rows.extend(table[1:])
            else:
                self.rows.extend(table)


def _append_table_without_duplicate_header(table, header, rows):
    current_header = table[0]

    if header is None:
        rows.extend(table)
        return current_header, rows

    if _same_row(current_header, header):
        rows.extend(table[1:])
    else:
        rows.extend(table)

    return header, rows


def _same_row(first_row, second_row):
    return _normalize_row(first_row) == _normalize_row(second_row)


def _normalize_row(row):
    return [" ".join(cell.lower().split()) for cell in row]


def _normalize_table(table):
    max_columns = max(len(row) for row in table)

    for row in table:
        missing = max_columns - len(row)

        if missing:
            row.extend([""] * missing)

    return table


_IDENTIFIER_PLAIN = re.compile(r"0\d+")
_IDENTIFIER_GROUPED = re.compile(r"0\d{1,2}(?:,\d{3})+")
_SIMPLE_INTEGER = re.compile(r"[+-]?\d+")
_TOKEN_SEPARATOR = re.compile(r"[\n\r\t ]")


def _normalize_nominal_values(dataframe):
    for column in dataframe.columns:
        column_series = dataframe[column]

        if _has_identifier_value(column_series):
            continue

        dataframe[column] = _normalize_nominal_column(column_series)

    return dataframe


def _has_identifier_value(column_series):
    cleaned_series = _clean_series_token(column_series)
    return bool(
        cleaned_series.str.fullmatch(_IDENTIFIER_PLAIN.pattern, na=False).any()
        or cleaned_series.str.fullmatch(_IDENTIFIER_GROUPED.pattern, na=False).any()
    )


def _clean_series_token(column_series):
    cleaned_series = column_series.map(lambda value: "" if value is None else str(value))
    cleaned_series = cleaned_series.str.strip().str.replace(_TOKEN_SEPARATOR.pattern, "", regex=True).str.rstrip("CDcd")
    return cleaned_series.mask(cleaned_series.isin(["-", "--", ".", ","]), "")


def _normalize_nominal_column(column_series):
    values = column_series.tolist()
    cleaned_series = _clean_series_token(column_series).tolist()
    result = []

    for index, value in enumerate(values):
        token = cleaned_series[index]

        if _SIMPLE_INTEGER.fullmatch(token):
            number = _to_simple_integer(token)
            result.append(value if number is None else number)
        else:
            result.append(_normalize_cell(value))

    return result


def _to_simple_integer(token):
    try:
        return int(token)
    except (ValueError, TypeError):
        return None


def _normalize_cell(value):
    if value is None:
        return None

    token = _clean_number_token(value)

    if not token:
        return value

    number = _to_number(token)
    return value if number is None else number


def _clean_number_token(value):
    token = str(value).strip()
    token = token.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    token = token.replace(" ", "")
    token = token.rstrip("CDcd")

    if token in {"-", "--", ".", ","}:
        return ""

    return token


def _to_number(value):
    token = _clean_number_token(value)

    if not token:
        return None

    lowered_token = token.lower()

    if "e" in lowered_token:
        mantissa, separator, exponent = lowered_token.partition("e")

        try:
            return _to_integral(Decimal(mantissa) * (Decimal(10) ** int(exponent)))
        except (InvalidOperation, ValueError):
            return None

    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", token):
        token = token.replace(".", "").replace(",", ".")
    else:
        token = token.replace(",", "")

    try:
        return _to_integral(Decimal(token))
    except InvalidOperation:
        return None


def _to_integral(number):
    integral = number.to_integral_value()
    return int(integral) if number == integral else float(number)
