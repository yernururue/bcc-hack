"""DOCX на входе и на выходе (опция reqcheck[docx]). Сеть не нужна: модель — FakeLLM."""

from pathlib import Path

import docx
import pytest
from test_pipeline import DOC, FakeLLM
from test_server import make_client, wait_done

from reqcheck import pipeline
from reqcheck.errors import InputError
from reqcheck.formats import detect_input, resolve_output
from reqcheck.ingest import ingest
from reqcheck.render.docx import markdown_to_docx
from reqcheck.settings import Settings


def make_docx(path: Path, text: str = DOC) -> Path:
    markdown_to_docx(text, path)
    return path


def docx_text(path: Path) -> str:
    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        parts += [c.text for row in t.rows for c in row.cells]
    return "\n".join(parts)


def test_docx_ingest_keeps_sections_lists_and_tables(tmp_path: Path) -> None:
    doc = ingest(detect_input(make_docx(tmp_path / "spec.docx")))
    assert doc.format == "docx"
    kinds = [(b.kind, b.anchor.item) for b in doc.blocks if b.kind != "heading"]
    assert ("list_item", "Ф-1") in kinds and ("list_item", "Ф-2") in kinds
    rows = [b for b in doc.blocks if b.kind == "table_row"]
    assert [r.text for r in rows] == ["Условие | Результат", "Порог 50 000 ₸ | Сохранено 5 000 ₸"]
    f1 = next(b for b in doc.blocks if b.anchor.item == "Ф-1")
    assert f1.anchor.section_path == ["Требования"]
    assert f1.anchor.line is None  # у Word нет строк: ссылка по разделу


def test_docx_heading_from_custom_style_name(tmp_path: Path) -> None:
    d = docx.Document()
    d.add_heading("Спецификация", level=0)  # стиль Title — название документа, не раздел
    d.add_heading("Требования", level=1)
    d.add_paragraph("Ф-1. Система хранит порог.", style="List Bullet")
    d.add_paragraph("Обычный абзац с текстом.")
    path = tmp_path / "w.docx"
    d.save(str(path))
    doc = ingest(detect_input(path))
    body = [b for b in doc.blocks if b.kind != "heading"]
    assert body[0].kind == "list_item" and body[0].anchor.section_path == ["Требования"]
    assert body[1].kind == "paragraph"


def test_not_a_docx_is_rejected(tmp_path: Path) -> None:
    fake = tmp_path / "x.docx"
    fake.write_text("просто текст", encoding="utf-8")
    with pytest.raises(InputError, match="не является DOCX"):
        detect_input(fake)


def test_pipeline_docx_in_docx_out_with_verified_quotes(tmp_path: Path) -> None:
    source = detect_input(make_docx(tmp_path / "spec.docx"))
    target = resolve_output(source.format, tmp_path / "out" / "report")
    assert target.format == "docx" and target.path.suffix == ".docx"
    options = pipeline.Options(model="fake/model", max_cost_usd=1.0)
    outcome = pipeline.run(source, target, options, Settings(_env_file=None), llm=FakeLLM())  # type: ignore[call-arg]
    report = outcome.report
    assert report is not None and report.findings
    text = docx_text(target.path)
    assert "7. Итог" in text
    # в DOCX-отчёт попадают ровно те цитаты, что проверил код
    for f in report.findings:
        assert f.verified
        assert f.quote in text


def test_server_accepts_docx_and_returns_docx_report(tmp_path: Path) -> None:
    data = make_docx(tmp_path / "spec.docx").read_bytes()
    c = make_client()
    job_id = c.post("/api/v1/checks", files={"file": ("spec.docx", data)}).json()["id"]
    assert wait_done(c, job_id)["status"] == "done"
    rep = c.get(f"/api/v1/checks/{job_id}/report")
    assert rep.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert "spec-review.docx" in rep.headers["content-disposition"]
    view = c.get(f"/api/v1/checks/{job_id}/summary").json()
    assert {fx["snippet"] for fx in view["fixes"]} <= {"text", None}
    assert any(fx["snippet"] == "text" for fx in view["fixes"])
