"""Цитата в исходнике (ADR-009): координаты в PDF, исходник с пометками, фрагменты оригинала."""

from pathlib import Path

import pymupdf
from test_pipeline import FakeLLM
from test_server import make_client, post, wait_done

from reqcheck import pipeline
from reqcheck.cli import main as cli_main
from reqcheck.formats import detect_input, resolve_output
from reqcheck.render.annotate import annotate_pdf
from reqcheck.schemas import Report
from reqcheck.settings import Settings
from reqcheck.stages.rects import find_rects

FIXTURES = Path(__file__).parent / "fixtures"


def _run_pdf(tmp_path: Path) -> Report:
    source = detect_input(FIXTURES / "defects.pdf")
    target = resolve_output(source.format, tmp_path / "r.json")
    options = pipeline.Options(model="fake/model", max_cost_usd=1.0)
    outcome = pipeline.run(source, target, options, Settings(_env_file=None), llm=FakeLLM())  # type: ignore[call-arg]
    assert outcome.report is not None
    return outcome.report


def test_rects_follow_line_wraps_and_skip_tiny_quotes() -> None:
    page = pymupdf.open(FIXTURES / "defects.pdf")[0]
    quote = "регистрировать возврат товара, пересчитывать бонусные баллы, отправлять покупателю"
    rects = find_rects(page, quote)
    assert len(rects) == 2 and all(r.page == 1 for r in rects)  # цитата на двух строках
    assert rects[0].y0 < rects[1].y0
    assert find_rects(page, "SMS") == []
    assert find_rects(page, "совсем другого текста здесь нет") == []


def test_pipeline_fills_rects_and_annotated_pdf(tmp_path: Path) -> None:
    report = _run_pdf(tmp_path)
    placed = [f for f in report.findings if f.anchor.rects]
    assert placed, "у находок PDF должны быть координаты цитат"
    target = tmp_path / "annotated.pdf"
    marks = annotate_pdf(FIXTURES / "defects.pdf", report, target)
    assert marks >= len(placed)
    doc = pymupdf.open(target)
    annots = [a for p in doc for a in p.annots()]
    assert len(annots) == marks
    first = placed[0]
    assert any(a.info["content"].startswith(first.finding_id) for a in annots)
    # текст исходника не изменился, только пометки
    assert doc[0].get_text() == pymupdf.open(FIXTURES / "defects.pdf")[0].get_text()


def test_cli_annotate_only_for_pdf(tmp_path: Path) -> None:
    md = tmp_path / "a.md"
    md.write_text("# Т\n\n* **Ф-1.** Текст требования.\n", encoding="utf-8")
    code = cli_main([str(md), str(tmp_path / "r.md"), "--annotate", str(tmp_path / "x.pdf")])
    assert code != 0


def test_server_annotated_and_snippets_for_pdf() -> None:
    c = make_client()
    data = (FIXTURES / "defects.pdf").read_bytes()
    job_id = post(c, name="spec.pdf", data=data).json()["id"]
    assert wait_done(c, job_id)["status"] == "done"
    ann = c.get(f"/api/v1/checks/{job_id}/annotated")
    assert ann.status_code == 200 and ann.content.startswith(b"%PDF-")
    assert "spec-annotated.pdf" in ann.headers["content-disposition"]
    report = Report.model_validate(c.get(f"/api/v1/checks/{job_id}/report?format=json").json())
    with_rects = [f for f in report.findings if f.anchor.rects]
    snip = c.get(f"/api/v1/checks/{job_id}/findings/{with_rects[0].finding_id}/snippet")
    assert snip.headers["content-type"] == "image/png"
    assert snip.content.startswith(b"\x89PNG")
    assert c.get(f"/api/v1/checks/{job_id}/findings/F-999/snippet").status_code == 404
    view = c.get(f"/api/v1/checks/{job_id}/summary").json()
    assert view["annotated"] is True
    kinds = {fx["id"]: fx["snippet"] for fx in view["fixes"]}
    assert all(kinds[f.finding_id] == "image" for f in with_rects if f.finding_id in kinds)


def test_server_snippet_paragraphs_for_markdown() -> None:
    c = make_client()
    job_id = post(c).json()["id"]
    assert wait_done(c, job_id)["status"] == "done"
    assert c.get(f"/api/v1/checks/{job_id}/annotated").status_code == 404
    report = Report.model_validate(c.get(f"/api/v1/checks/{job_id}/report?format=json").json())
    fid = report.findings[0].finding_id
    body = c.get(f"/api/v1/checks/{job_id}/findings/{fid}/snippet").json()
    assert body["finding_id"] == fid
    assert sum(p["highlight"] for p in body["paragraphs"]) == 1
    assert all({"text", "place", "highlight"} <= set(p) for p in body["paragraphs"])
    view = c.get(f"/api/v1/checks/{job_id}/summary").json()
    assert view["annotated"] is False
    assert {fx["snippet"] for fx in view["fixes"]} <= {"text", None}
