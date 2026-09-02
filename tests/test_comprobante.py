"""Tests for the spreadsheet writer and the reservation receipt."""

from __future__ import annotations

import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

from hotel_mcp.logic.comprobante import generar_comprobante
from hotel_mcp.xlsx import _column_name, write_sheet

from .conftest import TOMORROW, add_reservation

REQUIRED_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
    "xl/_rels/workbook.xml.rels",
    "xl/styles.xml",
    "xl/worksheets/sheet1.xml",
}


def test_column_names_roll_over_past_z() -> None:
    assert [_column_name(i) for i in (0, 1, 25, 26, 27, 51, 52)] == [
        "A", "B", "Z", "AA", "AB", "AZ", "BA",
    ]


def test_workbook_has_every_part_excel_requires(tmp_path: Path) -> None:
    target = write_sheet(tmp_path / "x.xlsx", [["a", 1]])

    with zipfile.ZipFile(target) as z:
        assert REQUIRED_PARTS <= set(z.namelist())
        assert z.testzip() is None
        for name in z.namelist():
            ET.fromstring(z.read(name))  # every part must be well-formed XML


def test_cells_are_escaped_and_typed(tmp_path: Path) -> None:
    target = write_sheet(
        tmp_path / "x.xlsx",
        [[("Titulo", "bold")], ["texto", "Ana & <Beto>"], ["numero", 740.5], ["vip", True]],
    )
    with zipfile.ZipFile(target) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()

    assert "Ana &amp; &lt;Beto&gt;" in sheet, "raw & or < would corrupt the XML"
    assert "<v>740.5</v>" in sheet, "numbers must not be written as inline strings"
    assert ">si<" in sheet, "a bool must read as a word, not as 1"
    assert 's="1"' in sheet, "the bold style must be applied"


def test_empty_cells_are_skipped(tmp_path: Path) -> None:
    target = write_sheet(tmp_path / "x.xlsx", [["a", None, ""], []])
    with zipfile.ZipFile(target) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert sheet.count("<c ") == 1


def test_receipt_reports_the_stay_and_writes_the_file(
    hotel: sqlite3.Connection, tmp_path: Path
) -> None:
    rid = add_reservation(
        hotel,
        huesped_id=2,
        habitacion_id=4,  # suite 501, 320.0 a night
        checkin=TOMORROW.isoformat(),
        checkout=(TOMORROW + timedelta(days=3)).isoformat(),
    )

    out = generar_comprobante(hotel, rid, destino=str(tmp_path / "c.xlsx"))

    assert out["noches"] == 3
    assert out["tarifa_por_noche"] == 320.0
    assert out["total_estimado"] == 960.0
    assert out["habitacion"] == "501"
    assert out["vip"] is True
    assert out["folio"] == f"R-{rid:05d}"
    assert Path(out["archivo"]).exists()

    with zipfile.ZipFile(out["archivo"]) as z:
        sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert "Beto VIP" in sheet
    assert "COMPROBANTE DE RESERVACION" in sheet


def test_receipt_works_before_a_room_is_assigned(
    hotel: sqlite3.Connection, tmp_path: Path
) -> None:
    """A guest can ask for proof of a booking that has not been roomed yet."""
    rid = add_reservation(hotel)

    out = generar_comprobante(hotel, rid, destino=str(tmp_path / "c.xlsx"))

    assert out["habitacion_asignada"] is False
    assert out["total_estimado"] is None
    with zipfile.ZipFile(out["archivo"]) as z:
        assert "sin asignar" in z.read("xl/worksheets/sheet1.xml").decode()


def test_receipt_rejects_an_unknown_reservation(hotel: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        generar_comprobante(hotel, 4242)


def test_default_filename_is_derived_from_folio_and_guest(
    hotel: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOTEL_EXPORT_DIR", str(tmp_path / "salida"))
    rid = add_reservation(hotel, huesped_id=3)  # "Caro Accesible"

    out = generar_comprobante(hotel, rid)

    name = Path(out["archivo"]).name
    assert name == f"comprobante-R-{rid:05d}-Caro-Accesible.xlsx"
    assert Path(out["archivo"]).parent.name == "salida"
