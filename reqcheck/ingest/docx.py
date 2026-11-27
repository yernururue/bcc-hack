"""DOCX → Markdown-текст для общего разбора (опция `reqcheck[docx]`, python-docx).

Документ Word переводится в тот же Markdown, что разбирает `MarkdownParser`: заголовки по
стилям, списки, таблицы. Так якоря, выделение требований и проверка цитат работают одинаково
для всех форматов. Номера строк у DOCX не ставим: в Word их нет, ссылкой служит путь раздела.
Автонумерация Word (1., 1.1) в текст абзаца не входит и теряется; номера, набранные текстом
(«Ф-1.»), сохраняются.
"""

import re
from pathlib import Path
from typing import Any

from reqcheck.errors import InputError

_HEADING_STYLE = re.compile(r"^(?:heading|заголовок)\s*(\d)$", re.IGNORECASE)
_TITLE_STYLES = {"title", "название", "заголовок"}
_LIST_HINTS = ("list", "список")


def _import_docx() -> Any:
    try:
        import docx
    except ImportError as exc:  # опциональная зависимость
        raise InputError(
            "Для DOCX нужна опция reqcheck[docx]: pip install 'reqcheck[docx]'"
        ) from exc
    return docx


def _heading_level(style: Any) -> int | None:
    """Уровень заголовка по стилю абзаца или его базовым стилям; None — не заголовок."""
    seen = 0
    while style is not None and seen < 10:
        name = (style.name or "").strip()
        if name.lower() in _TITLE_STYLES:
            return 1
        m = _HEADING_STYLE.match(name)
        if m:
            return max(1, min(6, int(m.group(1))))
        style = style.base_style
        seen += 1
    return None


def _is_list(paragraph: Any) -> bool:
    name = (paragraph.style.name if paragraph.style is not None else "").lower()
    if any(h in name for h in _LIST_HINTS):
        return True
    ppr = paragraph._p.pPr  # нумерация, заданная прямо в абзаце, без стиля списка
    return ppr is not None and ppr.numPr is not None


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _table(table: Any) -> list[str]:
    rows: list[list[str]] = []
    for row in table.rows:
        cells, prev = [], None
        for cell in row.cells:
            if cell._tc is prev:  # объединённая ячейка повторяется в row.cells
                continue
            prev = cell._tc
            cells.append(_cell(cell.text))
        if any(cells):
            rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return lines


def docx_to_markdown(path: Path) -> str:
    docx = _import_docx()
    from docx.table import Table

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # битый архив, не тот формат внутри
        raise InputError(f"Не удалось открыть DOCX: {path.name}") from exc
    out: list[str] = []
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            out += ["", *_table(item), ""]
            continue
        text = " ".join(item.text.split())
        if not text:
            continue
        level = _heading_level(item.style)
        if level is not None:
            out += ["", "#" * level + " " + text, ""]
        elif _is_list(item):
            out.append("- " + text)
        else:
            out += ["", text, ""]
    return "\n".join(out).strip() + "\n"
