"""OCR сканов (--ocr): проверки без Tesseract — его нет в CI, поэтому движок подменяем."""

from pathlib import Path

import pymupdf
import pytest

from reqcheck.config import OcrConfig
from reqcheck.errors import NoTextLayerError
from reqcheck.formats import detect_input
from reqcheck.ingest import ingest
from reqcheck.ingest import pdf as pdf_ingest


def scan(tmp_path: Path) -> Path:
    p = tmp_path / "scan.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(p)
    return p


def test_scan_without_ocr_suggests_flag(tmp_path: Path) -> None:
    with pytest.raises(NoTextLayerError, match="--ocr"):
        ingest(detect_input(scan(tmp_path)))


def test_missing_tesseract_is_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_tesseract(tessdata=None):
        raise RuntimeError("No tessdata specified and Tesseract is not installed")

    monkeypatch.setattr(pymupdf, "get_tessdata", no_tesseract)
    with pytest.raises(NoTextLayerError, match="нужен Tesseract"):
        ingest(detect_input(scan(tmp_path)), ocr=True)


def test_missing_language_data_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "eng.traineddata").write_bytes(b"x")
    monkeypatch.setattr(pymupdf, "get_tessdata", lambda tessdata_=None: str(tessdata))
    with pytest.raises(NoTextLayerError, match="rus"):
        pdf_ingest.tesseract_ocr(OcrConfig(language="rus+eng"))


def test_ocr_options_reach_pymupdf4llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pymupdf4llm

    engine = object()
    seen: dict = {}

    def fake_to_markdown(doc, **kwargs):
        seen.update(kwargs)
        text = "## Требования\n\n- Ф-1. Система хранит порог.\n"
        return [{"metadata": {"page_number": 1}, "text": text}]

    monkeypatch.setattr(pdf_ingest, "tesseract_ocr", lambda cfg: engine)
    monkeypatch.setattr(pymupdf4llm, "to_markdown", fake_to_markdown)
    doc = ingest(detect_input(scan(tmp_path)), ocr=True)
    assert seen["use_ocr"] is True and seen["ocr_function"] is engine
    assert seen["ocr_language"] == "rus+eng" and seen["ocr_dpi"] == 300
    assert any(b.anchor.item == "Ф-1" for b in doc.blocks)


def test_ocr_that_finds_nothing_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pymupdf4llm

    monkeypatch.setattr(pdf_ingest, "tesseract_ocr", lambda cfg: object())
    monkeypatch.setattr(
        pymupdf4llm,
        "to_markdown",
        lambda doc, **kw: [{"metadata": {"page_number": 1}, "text": ""}],
    )
    with pytest.raises(NoTextLayerError, match="OCR не распознал"):
        ingest(detect_input(scan(tmp_path)), ocr=True)
