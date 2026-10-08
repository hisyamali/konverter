import csv
import math
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, TimeoutError, as_completed
from concurrent.futures.process import BrokenProcessPool
from decimal import Decimal, InvalidOperation

import pandas as pd
import pdfplumber
from openpyxl import Workbook

PARALLEL_MIN_PAGES = 12
PARALLEL_MAX_WORKERS = int(os.environ.get("KONVERTER_MAX_WORKERS", "4"))
PARALLEL_CHUNK_PAGES = int(os.environ.get("KONVERTER_CHUNK_PAGES", "20"))
PARALLEL_MAX_TASKS_PER_CHILD = 8
PARALLEL_STALL_REPORT_SECONDS = 45
PARALLEL_STALL_FAIL_SECONDS = 180

PDF_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 5,
    "join_tolerance": 5,
    "edge_min_length": 5,
}

_WORKER_PDF = None


def convert_csv_to_excel(input_path, output_path, progress=None):
    _report(progress, 12, "Membaca file CSV")
    dataframe = _read_csv(input_path)

    if dataframe.empty:
        raise ValueError("File CSV tidak memiliki data.")

    _report(progress, 70, "Menyusun kolom data")

    _normalize_nominal_values(dataframe)

    _report(progress, 88, "Menulis file Excel")
    _write_excel(dataframe, output_path)

    _report(progress, 100, "Selesai")


def _write_excel(dataframe, output_path, sheet_name="Sheet1", include_header=True):
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet(sheet_name)

    if include_header:
        sheet.append(list(dataframe.columns))

    for row in dataframe.itertuples(index=False, name=None):
        sheet.append(row)

    workbook.save(output_path)


def convert_pdf_to_excel(input_path, output_path, progress=None, total_pages=None):
    if total_pages is None:
        _report(progress, 6, "Membuka file PDF")

    tables = _collect_pdf_tables(input_path, progress, total_pages)

    rows = []
    header = None

    for table in tables:
        header, rows = _append_table_without_duplicate_header(table, header, rows)

    if not rows:
        raise ValueError("Tidak ada tabel yang bisa dibaca dari PDF.")

    _report(progress, 86, "Menyusun kolom data")

    rows = _normalize_table(rows)
    dataframe = pd.DataFrame(rows)
    _normalize_nominal_values(dataframe)

    _report(progress, 93, "Menulis file Excel")
    _write_excel(dataframe, output_path, sheet_name="Data", include_header=False)

    _report(progress, 100, "Selesai")


def _collect_pdf_tables(input_path, progress, total_pages=None):
    if total_pages is None:
        total_pages = _count_pages(input_path)

    workers = _worker_count(total_pages)

    _report(progress, 8, f"Halaman 0 dari halaman {total_pages}")

    if workers == 1:
        return _read_pages_sequential(input_path, total_pages, progress)

    return _read_chunks_parallel(input_path, _build_chunks(total_pages), workers, total_pages, progress)


def _count_pages(input_path):
    with pdfplumber.open(input_path) as pdf:
        return len(pdf.pages)


def _worker_count(total_pages):
    if total_pages <= PARALLEL_MIN_PAGES:
        return 1

    return max(1, min(os.cpu_count() or 1, PARALLEL_MAX_WORKERS))


def _build_chunks(total_pages):
    return [
        (start, min(start + PARALLEL_CHUNK_PAGES, total_pages))
        for start in range(0, total_pages, PARALLEL_CHUNK_PAGES)
    ]


def _read_pages_sequential(input_path, total_pages, progress):
    tables = []
    pages_read = 0

    with pdfplumber.open(input_path) as pdf:
        for page in pdf.pages:
            tables.extend(_page_tables(page))
            pages_read += 1
            _report(progress, _percent_from_pages(pages_read, total_pages), _status_message(pages_read, total_pages))

    return tables


def _read_chunks_parallel(input_path, chunks, workers, total_pages, progress):
    results = {}
    pages_read = 0
    last_success = time.monotonic()

    executor = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_pdf_worker,
        initargs=(str(input_path),),
        max_tasks_per_child=PARALLEL_MAX_TASKS_PER_CHILD,
    )
    futures = {executor.submit(_extract_chunk_task, chunk): chunk for chunk in chunks}
    pending = set(futures)

    try:
        while pending:
            try:
                for future in as_completed(pending, timeout=PARALLEL_STALL_REPORT_SECONDS):
                    start, chunk_tables = future.result()
                    results[start] = chunk_tables
                    chunk_start, chunk_end = futures[future]
                    pages_read += chunk_end - chunk_start
                    pending.remove(future)
                    last_success = time.monotonic()
                    _report(progress, _percent_from_pages(pages_read, total_pages), _status_message(pages_read, total_pages))
            except TimeoutError:
                stalled_for = int(time.monotonic() - last_success)
                _report(
                    progress,
                    _percent_from_pages(pages_read, total_pages),
                    f"{_status_message(pages_read, total_pages)} — pembacaan melambat (±{stalled_for} detik), tetap berjalan",
                )

                if stalled_for >= PARALLEL_STALL_FAIL_SECONDS:
                    _abort_executor(executor, pending)
                    raise RuntimeError(
                        f"Pembacaan PDF terhenti (tidak ada kemajuan sejak halaman {pages_read}). "
                        f"Mesin sibuk atau kehabisan memori. Tutup aplikasi lain lalu coba lagi; "
                        f"opsional turunkan beban dengan KONVERTER_MAX_WORKERS={max(2, workers // 2)}."
                    )
    except BrokenProcessPool:
        _abort_executor(executor, pending)
        raise RuntimeError("Proses pembacaan PDF terhenti. Silakan coba lagi.")
    else:
        executor.shutdown(wait=True)

    ordered = []

    for start in sorted(results):
        ordered.extend(results[start])

    return ordered


def _abort_executor(executor, pending):
    for future in pending:
        future.cancel()

    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except ValueError:
        pass


def _init_pdf_worker(pdf_path):
    global _WORKER_PDF
    _WORKER_PDF = pdfplumber.open(pdf_path)


def _extract_chunk_task(chunk):
    start, end = chunk
    return start, _extract_chunk(_WORKER_PDF, start, end)


def _page_tables(page):
    tables = []

    for table in page.extract_tables(PDF_TABLE_SETTINGS):
        cleaned_table = _clean_table(table)

        if cleaned_table:
            tables.append(cleaned_table)

    return tables


def _extract_chunk(pdf, start, end):
    tables = []

    for page in pdf.pages[start:end]:
        tables.extend(_page_tables(page))

    return tables


def _percent_from_pages(pages_read, total_pages):
    return 8 + int(76 * pages_read / max(total_pages, 1))


def _status_message(pages_read, total_pages):
    return f"Halaman {pages_read} dari halaman {total_pages}"


def _report(progress, percent, message):
    if progress is None:
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
    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)

        if page_count == 0:
            raise ValueError("Tidak ada halaman yang bisa dibaca dari PDF.")

        preview = pdf.pages[0].extract_tables(PDF_TABLE_SETTINGS)
        sample_rows = []

        if preview:
            for row in preview[0][:PREVIEW_PDF_TABLE_ROWS]:
                sample_rows.append(
                    [(cell or "").strip() if cell else "" for cell in row]
                )

        if page_count > 1:
            page_count_timed = min(PREVIEW_TIMED_PAGES, page_count)
            start = min(page_count // 2, page_count - page_count_timed)
            timed_start = time.perf_counter()
            timed_pages = 0

            for index in range(start, start + page_count_timed):
                try:
                    pdf.pages[index].extract_tables(PDF_TABLE_SETTINGS)
                except Exception:
                    continue

                timed_pages += 1

            elapsed_sample = time.perf_counter() - timed_start
            per_page_seconds = elapsed_sample / max(timed_pages, 1)
        else:
            timed_pages = 0
            per_page_seconds = 0.0

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
        return pd.DataFrame()

    delimiter = _sniff_delimiter(input_path)

    if delimiter is not None:
        try:
            return pd.read_csv(input_path, sep=delimiter, engine="c")
        except Exception:
            try:
                return pd.read_csv(input_path, sep=delimiter, engine="c", encoding="latin-1")
            except Exception:
                pass

    try:
        return pd.read_csv(input_path, sep=None, engine="python")
    except UnicodeDecodeError:
        return pd.read_csv(input_path, sep=None, engine="python", encoding="latin-1")


def _has_content(input_path):
    whitespace = b"\x00\r\n\t "
    try:
        with open(input_path, "rb") as file:
            while True:
                chunk = file.read(65536)

                if not chunk:
                    return False

                if any(byte not in whitespace for byte in chunk):
                    return True
    except OSError:
        return True


def _sniff_delimiter(input_path):
    try:
        with open(input_path, "rb") as file:
            sample = file.read(8192)
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
    length = len(line)

    while index < length:
        current = line[index]

        if current == '"':
            if in_quotes and index + 1 < length and line[index + 1] == '"':
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

    return pd.DataFrame(rows, columns=columns)


def _read_text_lines(input_path):
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with open(input_path, encoding=encoding, newline="") as file:
                return file.read().splitlines()
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


def _append_table_without_duplicate_header(table, header, rows):
    current_header = table[0]

    if header is None:
        return current_header, rows + table

    data_rows = table[1:] if _same_row(current_header, header) else table
    return header, rows + data_rows


def _same_row(first_row, second_row):
    return _normalize_row(first_row) == _normalize_row(second_row)


def _normalize_row(row):
    return [" ".join(cell.lower().split()) for cell in row]


def _normalize_table(table):
    max_columns = max(len(row) for row in table)
    return [row + [""] * (max_columns - len(row)) for row in table]


_IDENTIFIER_PLAIN = re.compile(r"0\d+")
_IDENTIFIER_GROUPED = re.compile(r"0\d{1,2}(?:,\d{3})+")
_SIMPLE_INTEGER = re.compile(r"[+-]?\d+")
_TOKEN_SEPARATOR = re.compile(r"[\n\r\t ]")


def _normalize_nominal_values(dataframe):
    for column in dataframe.columns:
        series = dataframe[column]

        if _has_identifier_value(series):
            continue

        dataframe[column] = _normalize_nominal_column(series)

    return dataframe


def _has_identifier_value(series):
    cleaned = _clean_series_token(series)
    return bool(
        cleaned.str.fullmatch(_IDENTIFIER_PLAIN.pattern, na=False).any()
        or cleaned.str.fullmatch(_IDENTIFIER_GROUPED.pattern, na=False).any()
    )


def _clean_series_token(series):
    cleaned = series.map(lambda value: "" if value is None else str(value))
    cleaned = cleaned.str.strip().str.replace(_TOKEN_SEPARATOR.pattern, "", regex=True).str.rstrip("CDcd")
    return cleaned.mask(cleaned.isin(["-", "--", ".", ","]), "")


def _normalize_nominal_column(series):
    values = series.tolist()
    cleaned = _clean_series_token(series).tolist()
    result = []

    for index, value in enumerate(values):
        token = cleaned[index]

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

    lowered = token.lower()

    if "e" in lowered:
        mantissa, _, exponent = lowered.partition("e")

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
