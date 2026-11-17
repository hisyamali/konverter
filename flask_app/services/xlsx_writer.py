"""Penulis XLSX streaming yang cepat dan hemat memori.

Writer ini menulis satu worksheet langsung ke arsip ZIP tanpa membangun model
workbook di memori. Nilai sel dipetakan sama seperti perilaku openpyxl pada
pemakaian di aplikasi ini:

- ``None`` tidak menghasilkan sel, sedangkan string kosong menghasilkan sel
  kosong; keduanya terbaca kembali sebagai ``None`` seperti pada openpyxl.
- ``bool`` ditulis sebagai sel boolean.
- ``int``/``float``/``Decimal`` ditulis sebagai sel angka; NaN dan tak hingga
  menjadi sel kosong (openpyxl membacanya kembali sebagai ``None``).
- String yang diawali ``=`` dan panjangnya lebih dari satu karakter ditulis
  sebagai rumus, sama seperti openpyxl.
- Karakter kontrol yang tidak sah ditolak dengan ``ValueError``, sama seperti
  ``IllegalCharacterError`` openpyxl.

Struktur arsip mengikuti OOXML minimal sehingga tetap bisa dibuka Excel,
LibreOffice, dan openpyxl tanpa peringatan.
"""

import io
import math
import re
import zipfile
from decimal import Decimal
from xml.sax.saxutils import escape, quoteattr

ILLEGAL_CHARACTERS_RE = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")
ERROR_CODES = frozenset(
    ("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A")
)
MAX_CELL_STRING = 32767

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

# styles.xml memuat cellStyles agar openpyxl tidak memperingatkan "Workbook
# contains no default style", dan dokumen tetap dianggap valid oleh Excel.
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font><sz val="11"/><name val="Calibri"/><family val="2"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
<dxfs count="0"/>
<tableStyles count="0" defaultTableStyle="TableStyleMedium9" defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>"""

_WORKSHEET_OPEN = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    "<sheetData>"
)
_WORKSHEET_CLOSE = "</sheetData></worksheet>"

_COLUMN_LETTERS = []


def _column_letter(index):
    """Huruf kolom untuk indeks 0-based, di-cache agar tidak dihitung ulang."""

    letters = _COLUMN_LETTERS

    while len(letters) <= index:
        number = len(letters) + 1
        name = ""

        while number:
            number, remainder = divmod(number - 1, 26)
            name = chr(65 + remainder) + name

        letters.append(name)

    return letters[index]


def _string_cell(ref, text):
    text = text[:MAX_CELL_STRING]

    if ILLEGAL_CHARACTERS_RE.search(text):
        raise ValueError(f"{text} cannot be used in worksheets.")

    space = ' xml:space="preserve"' if text != text.strip() else ""

    if len(text) > 1 and text.startswith("="):
        return f'<c r="{ref}"><f>{escape(text[1:])}</f></c>'

    if text in ERROR_CODES:
        return f'<c r="{ref}" t="e"><v>{escape(text)}</v></c>'

    return f'<c r="{ref}" t="inlineStr"><is><t{space}>{escape(text)}</t></is></c>'


def _number_cell(ref, value):
    if value != value or value == math.inf or value == -math.inf:
        return f'<c r="{ref}" t="n"/>'

    # openpyxl memformat semua angka dengan "%.16g", jadi ikuti agar nilai
    # yang terbaca (termasuk angka besar dan -0.0) tetap identik.
    return f'<c r="{ref}" t="n"><v>{"%.16g" % value}</v></c>'


def _cell_xml(ref, value):
    kind = type(value)

    if kind is str:
        if not value:
            return f'<c r="{ref}" t="inlineStr"/>'
        return _string_cell(ref, value)

    if value is None:
        return ""

    if kind is bool:
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'

    if kind is int:
        return _number_cell(ref, value)

    if kind is float:
        return _number_cell(ref, value)

    if isinstance(value, Decimal):
        return _number_cell(ref, value)

    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'

    if isinstance(value, int):
        return _number_cell(ref, value)

    if isinstance(value, float):
        return _number_cell(ref, value)

    item = getattr(value, "item", None)

    if item is not None and type(value).__module__.startswith("numpy"):
        converted = item()

        if type(converted) is not type(value):
            return _cell_xml(ref, converted)

    return _string_cell(ref, str(value))


def _iter_sheet_xml(rows, columns):
    letter = _column_letter
    build_cell = _cell_xml
    buffer = [_WORKSHEET_OPEN]
    row_number = 0

    if columns is not None:
        row_number += 1
        cells = [build_cell(f"{letter(index)}{row_number}", name) for index, name in enumerate(columns)]
        buffer.append(f'<row r="{row_number}">{"".join(cells)}</row>')

    for row in rows:
        row_number += 1
        cells = []

        for index, value in enumerate(row):
            if value is None:
                continue

            cell = build_cell(f"{letter(index)}{row_number}", value)

            if cell:
                cells.append(cell)

        buffer.append(f'<row r="{row_number}">{"".join(cells)}</row>')

        if len(buffer) >= 2048:
            yield "".join(buffer)
            buffer.clear()

    buffer.append(_WORKSHEET_CLOSE)
    yield "".join(buffer)


def _workbook_xml(sheet_name):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name={quoteattr(sheet_name)} '
        'sheetId="1" r:id="rId1"/></sheets></workbook>'
    )


def write_xlsx(rows, output_path, columns=None, sheet_name="Sheet1", on_row=None, progress_step=0):
    """Tulis satu worksheet dari ``rows`` (iterable berisi urutan nilai).

    ``columns`` bila diberikan ditulis sebagai baris pertama. ``on_row``
    dipanggil setiap ``progress_step`` baris data dengan jumlah baris yang
    sudah ditulis, dipakai untuk pelaporan progres tanpa mengubah isi berkas.
    """

    with zipfile.ZipFile(
        output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1
    ) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        archive.writestr("xl/workbook.xml", _workbook_xml(sheet_name))
        archive.writestr("xl/styles.xml", _STYLES)

        tracked = on_row is not None and progress_step > 0

        if tracked:
            source = _track_rows(rows, on_row, progress_step)
        else:
            source = rows

        with archive.open("xl/worksheets/sheet1.xml", "w") as handle:
            writer = io.TextIOWrapper(handle, encoding="utf-8", newline="")

            try:
                for chunk in _iter_sheet_xml(source, columns):
                    writer.write(chunk)
            finally:
                writer.flush()
                writer.detach()


def _track_rows(rows, on_row, step):
    for index, row in enumerate(rows, start=1):
        if index % step == 0:
            on_row(index)

        yield row