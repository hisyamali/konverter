"""Rutin ekstraksi PDF yang dijalankan di proses worker.

Modul ini sengaja diletakkan di root paket (bukan di dalam ``flask_app``) supaya
proses worker tidak perlu mengimpor Flask, pandas, openpyxl, atau store akun.
Pada Windows proses worker dibuat dengan cara ``spawn`` sehingga setiap worker
mengimpor ulang modulnya; menjaga modul ini tetap ringan memangkas beberapa
detik waktu mulai untuk konversi PDF berukuran besar.
"""

import os
import re

import pdfplumber

try:
    import pymupdf
except ImportError:
    pymupdf = None


_FAST_WORDS_ENABLED = os.environ.get("KONVERTER_PDF_WORDS", "1") not in ("0", "false", "False")
FAST_WORDS_AVAILABLE = pymupdf is not None and _FAST_WORDS_ENABLED

POLITE_MODE = os.environ.get("KONVERTER_POLITE", "1") not in ("0", "false", "False")
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


def _open_text_document(input_path):
    if FAST_WORDS_AVAILABLE:
        return pymupdf.open(str(input_path))

    return pdfplumber.open(str(input_path))


def _iter_pages(document, start=0):
    page_list = getattr(document, "pages", None)

    if isinstance(page_list, list):
        return page_list[start:]

    return (document[index] for index in range(start, len(document)))


def _iter_text_pages(document, start, end):
    page_list = getattr(document, "pages", None)

    if isinstance(page_list, list):
        return page_list[start:end]

    return (document[index] for index in range(start, min(end, len(document))))


def _page_words(page):
    if hasattr(page, "extract_words"):
        return page.extract_words()

    return [
        {"text": word[4], "x0": word[0], "top": word[1]}
        for word in page.get_text("words")
    ]


def _clean_table(table):
    cleaned_rows = []

    for row in table:
        if row is None:
            continue

        cleaned_row = [(cell or "").strip() for cell in row]
        if any(cleaned_row):
            cleaned_rows.append(cleaned_row)

    return cleaned_rows


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


def _init_pdf_worker(pdf_path):
    _apply_polite_priority()
    global _WORKER_PDF
    _WORKER_PDF = pdfplumber.open(pdf_path)


def _init_text_worker(pdf_path):
    _apply_polite_priority()
    global _WORKER_PDF
    _WORKER_PDF = _open_text_document(pdf_path)


def _extract_chunk_task(chunk):
    start, end = chunk
    return start, _extract_chunk(_WORKER_PDF, start, end)


def _extract_text_chunk_task(chunk):
    start, end = chunk
    rows = []

    for page in _iter_text_pages(_WORKER_PDF, start, end):
        rows.extend(_extract_text_page(page))
    return start, rows
