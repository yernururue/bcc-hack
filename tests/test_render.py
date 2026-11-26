"""PDF-отчёт: кириллица и казахские буквы на месте, шрифты встроены, ссылки работают."""

from pathlib import Path

import pymupdf
import pytest
from test_pipeline import run

from reqcheck.formats import resolve_output
from reqcheck.render import render_markdown, write
from reqcheck.render.pdf import render_pdf
from reqcheck.schemas import Report

SECTIONS = (
    "1. Паспорт прогона",
    "2. Как агент нарезал требования",
    "3. Проверка каждого требования",
    "4. Проверка всего набора",
    "5. Сводка",
    "6. Вопросы к автору документа",
    "7. Итог",
)


@pytest.fixture
def report(tmp_path: Path) -> Report:
    r = run(tmp_path)[0].report
    assert r is not None
    doc = r.run.document.model_copy(update={"name": "талаптар_қазақша.md"})
    return r.model_copy(update={"run": r.run.model_copy(update={"document": doc})})


@pytest.fixture
def pdf(report: Report, tmp_path: Path) -> pymupdf.Document:
    path = tmp_path / "r.pdf"
    render_pdf(report, path)
    return pymupdf.open(path)


def text(doc: pymupdf.Document) -> str:
    return " ".join(" ".join(p.get_text().split()) for p in doc)


def test_pdf_has_all_sections_and_unicode(pdf: pymupdf.Document) -> None:
    t = text(pdf)
    for title in SECTIONS:
        assert title in t
    assert "қазақша" in t
    assert "₸" in t
    assert "R-001" in t and "Ф-1" in t


def test_pdf_fonts_embedded(pdf: pymupdf.Document) -> None:
    fonts = {f[3].split("+")[-1] for p in pdf for f in p.get_fonts(full=True)}  # ABCDEF+имя
    assert {"Golos Text Regular", "Golos Text SemiBold", "JetBrains Mono Regular"} <= fonts
    embedded = {f[1] for p in pdf for f in p.get_fonts(full=True) if "Golos" in f[3]}
    assert embedded and "n/a" not in embedded  # ext = ttf/cff: шрифт встроен в файл


def test_pdf_internal_links(pdf: pymupdf.Document) -> None:
    links = [lk for p in pdf for lk in p.get_links()]
    assert links and all(lk["kind"] == pymupdf.LINK_GOTO for lk in links)
    assert all(0 <= lk["page"] < pdf.page_count for lk in links)


def test_pdf_footer_on_every_page(pdf: pymupdf.Document) -> None:
    n = pdf.page_count
    for i, p in enumerate(pdf, start=1):
        assert f"стр. {i} из {n}" in p.get_text()


def test_pdf_and_md_show_same_items(report: Report, pdf: pymupdf.Document) -> None:
    md, t = render_markdown(report), text(pdf)
    for item in [r.report_id for r in report.requirements] + [
        f.finding_id for f in report.findings
    ]:
        assert item in md and item in t


def test_write_pdf_with_json(report: Report, tmp_path: Path) -> None:
    target = resolve_output("pdf", tmp_path / "out" / "r.pdf")
    written = write(report, target)
    assert target.path in written and target.json_path in written
    assert target.path.read_bytes().startswith(b"%PDF")
    assert Report.model_validate_json(target.json_path.read_text(encoding="utf-8")) == report


def test_pdf_text_layer_keeps_latin_a(report: Report, tmp_path: Path) -> None:
    """ADR-010: альтернативы глифов (Golos `locl`: A → A.1) есть в ToUnicode, «A» не станет «Ă»."""
    doc = report.run.document.model_copy(update={"name": "API Alerts ABC.md"})
    r = report.model_copy(update={"run": report.run.model_copy(update={"document": doc})})
    path = tmp_path / "a.pdf"
    render_pdf(r, path)
    t = text(pymupdf.open(path))
    assert "API Alerts ABC" in t
    assert "Ă" not in t


def test_patch_cmap_adds_only_missing_codes() -> None:
    from reqcheck.render.tounicode import patch_cmap

    cmap = b"begincmap\n1 beginbfrange\n<0001> <0003> <0041>\nendbfrange\nendcmap\nend"
    out = patch_cmap(cmap, {2: "Z", 258: "A"})
    assert b"<0102> <0041>" in out and b"<0002> <005A>" not in out
    assert out.endswith(b"endcmap\nend")
    assert patch_cmap(out, {258: "A"}) == out
