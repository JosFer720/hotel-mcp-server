"""Minimal .xlsx writer using standard library zipfile."""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

Cell = Any
"""One cell value."""

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

# Cell format styles
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="1"><fill><patternFill patternType="none"/></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
</styleSheet>"""


def _column_name(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell_xml(ref: str, value: Cell) -> str:
    style = "0"
    if isinstance(value, tuple):
        value, marker = value
        style = "1" if marker == "bold" else "0"

    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        # Checked before int: bool is a subclass of int, and writing True as 1
        # loses the meaning of a VIP flag.
        value = "si" if value else "no"
    elif isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'

    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'


def _sheet_xml(rows: list[list[Cell]], widths: list[float] | None) -> str:
    cols = ""
    if widths:
        entries = "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths)
        )
        cols = f"<cols>{entries}</cols>"

    body = []
    for r, row in enumerate(rows, start=1):
        cells = "".join(_cell_xml(f"{_column_name(c)}{r}", v) for c, v in enumerate(row))
        body.append(f'<row r="{r}">{cells}</row>')

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'{cols}<sheetData>{"".join(body)}</sheetData></worksheet>'
    )


def write_sheet(
    path: Path | str,
    rows: list[list[Cell]],
    *,
    title: str = "Hoja1",
    widths: list[float] | None = None,
) -> Path:
    """Write ``rows`` to an .xlsx file and return the path it was written to.

    A cell may be a string, a number, a bool, ``None`` for an empty cell, or a
    ``(value, "bold")`` pair. Parent directories are created as needed.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(title[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )

    # Fixed timestamp for deterministic zip creation
    stamp = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in (
            ("[Content_Types].xml", _CONTENT_TYPES),
            ("_rels/.rels", _ROOT_RELS),
            ("xl/workbook.xml", workbook),
            ("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS),
            ("xl/styles.xml", _STYLES),
            ("xl/worksheets/sheet1.xml", _sheet_xml(rows, widths)),
        ):
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return target


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")
