"""Report → DOCX (опция `reqcheck[docx]`): тот же текст, что в Markdown-отчёте.

Источник — наш собственный Markdown-отчёт (render_markdown), а не отдельный шаблон: содержимое
форматов совпадает, и цитаты в DOCX те же, что прошли проверку кодом. Разбираем только
конструкции, которые порождает `templates/report.md.j2`: заголовки, таблицы, цитаты `>`,
списки, абзацы, **жирный** и `код` внутри строки.
"""

import re
from pathlib import Path
from typing import Any

from reqcheck.ingest.docx import _import_docx
from reqcheck.render.markdown import render_markdown
from reqcheck.schemas import Report

FONT = "Arial"  # есть в Word и LibreOffice, кириллица и ₸ на месте
MONO = "Consolas"
_INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_TABLE_SEP = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$")


def _runs(paragraph: Any, text: str) -> None:
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("`") and part.endswith("`"):
            paragraph.add_run(part[1:-1]).font.name = MONO
        else:
            paragraph.add_run(part)


def _cells(line: str) -> list[str]:
    inner = line.strip().removeprefix("|").removesuffix("|")
    return [c.strip().replace("\\|", "|") for c in _CELL_SPLIT.split(inner)]


def _add_table(doc: Any, rows: list[list[str]]) -> None:
    width = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=width, style="Table Grid")
    for i, row in enumerate(rows):
        for j in range(width):
            cell = table.rows[i].cells[j]
            par = cell.paragraphs[0]
            _runs(par, row[j] if j < len(row) else "")
            if i == 0:
                for run in par.runs:
                    run.bold = True


def markdown_to_docx(text: str, target: Path) -> None:
    docx = _import_docx()
    doc = docx.Document()
    doc.styles["Normal"].font.name = FONT
    lines = text.splitlines()
    i = 0
    in_code: list[str] | None = None
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            if in_code is None:
                in_code = []
            else:
                par = doc.add_paragraph()
                par.add_run("\n".join(in_code)).font.name = MONO
                in_code = None
            i += 1
            continue
        if in_code is not None:
            in_code.append(line)
            i += 1
            continue
        if line.startswith("|"):
            block: list[list[str]] = []
            while i < len(lines) and lines[i].startswith("|"):
                if not _TABLE_SEP.match(lines[i].strip()):
                    block.append(_cells(lines[i]))
                i += 1
            if block:
                _add_table(doc, block)
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            doc.add_heading(m.group(2).strip(), level=min(len(m.group(1)) - 1, 9) or 0)
        elif line.startswith("> "):
            _runs(doc.add_paragraph(style="Quote"), line[2:])
        elif re.match(r"^\s*[-*] ", line):
            _runs(doc.add_paragraph(style="List Bullet"), re.sub(r"^\s*[-*] ", "", line))
        elif re.match(r"^\s*\d+\. ", line):
            _runs(doc.add_paragraph(style="List Number"), re.sub(r"^\s*\d+\. ", "", line))
        elif line.strip():
            _runs(doc.add_paragraph(), line)
        i += 1
    target.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(target))


def render_docx(report: Report, target: Path) -> None:
    markdown_to_docx(render_markdown(report), target)
