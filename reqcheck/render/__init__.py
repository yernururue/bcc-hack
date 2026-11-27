"""Этап render: Report → MD / PDF + JSON рядом. Содержимое MD и PDF одинаковое (общий контекст)."""

from pathlib import Path

from reqcheck.formats import OutputTarget
from reqcheck.render.markdown import render_markdown
from reqcheck.schemas import Report

__all__ = ["render_markdown", "write"]


def write(report: Report, target: OutputTarget) -> list[Path]:
    """Пишет отчёт в формате target и JSON рядом. Возвращает записанные пути."""
    target.path.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if target.format == "md":
        target.path.write_text(render_markdown(report), encoding="utf-8")
        written.append(target.path)
    elif target.format == "pdf":
        from reqcheck.render.pdf import render_pdf

        render_pdf(report, target.path)
        written.append(target.path)
    target.json_path.parent.mkdir(parents=True, exist_ok=True)
    target.json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    if target.json_path not in written:
        written.append(target.json_path)
    return written
