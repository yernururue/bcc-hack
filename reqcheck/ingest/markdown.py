"""Разбор Markdown/TXT в блоки с якорями: заголовки, абзацы, списки, таблицы, код.

Тот же разбор применяется к Markdown, который pymupdf4llm строит из страниц PDF.
"""

import re
from dataclasses import dataclass, field

from reqcheck.config import IngestConfig
from reqcheck.ingest.normalize import squash_spaces
from reqcheck.schemas.document import BlockKind

_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HR = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
_BULLET = re.compile(r"^(\s*)[*+\-]\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d{1,9})[.)]\s+(.*)$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?\s*$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_BLOCKQUOTE = re.compile(r"^\s*>\s?")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


@dataclass
class RawBlock:
    kind: BlockKind
    text: str
    page: int | None
    line: int | None
    section: tuple[str, ...]
    item: str | None = None
    level: int = 0  # уровень заголовка; 0 для остальных блоков


@dataclass
class ParseState:
    """Состояние, которое переживает границу страниц PDF: открытые заголовки."""

    headings: list[tuple[int, str]] = field(default_factory=list)

    @property
    def section(self) -> tuple[str, ...]:
        return tuple(text for _, text in self.headings)

    def open_heading(self, level: int, text: str) -> None:
        while self.headings and self.headings[-1][0] >= level:
            self.headings.pop()
        self.headings.append((level, text))


def clean_inline(text: str) -> str:
    """Убирает разметку, которой нет в «видимом» тексте: **, `, <br>, экранирование."""
    text = _BR.sub(" ", text)
    text = text.replace("**", "").replace("`", "")
    text = text.replace("\\|", "|").replace("\\*", "*").replace("\\_", "_")
    return squash_spaces(text)


@dataclass
class _Open:
    kind: BlockKind
    parts: list[str]
    line: int
    item: str | None


class MarkdownParser:
    def __init__(self, cfg: IngestConfig) -> None:
        self._cfg = cfg
        self._patterns = cfg.compiled_item_patterns()

    def match_item(self, raw: str) -> str | None:
        raw = raw.lstrip()
        for pattern in self._patterns:
            m = pattern.match(raw)
            if m:
                return clean_inline(m.group("id")) or None
        return None

    def table_item(self, raw_cell: str) -> str | None:
        found = self.match_item(raw_cell)
        if found:
            return found
        cell = clean_inline(raw_cell)
        if (
            cell
            and len(cell) <= self._cfg.table_item_max_len
            and not any(ch.isspace() for ch in cell)
        ):
            return cell
        return None

    def parse(
        self, text: str, state: ParseState, *, page: int | None, with_lines: bool
    ) -> list[RawBlock]:
        out: list[RawBlock] = []
        lines = text.split("\n")
        current: _Open | None = None
        # Номера пунктов открытых уровней списка: (отступ, номер) — вложенные наследуют номер.
        list_ids: list[tuple[int, str | None]] = []

        def emit(kind: BlockKind, body: str, lineno: int, item: str | None, level: int = 0):
            body = body.strip()
            if not body:
                return
            out.append(
                RawBlock(
                    kind=kind,
                    text=body,
                    page=page,
                    line=lineno if with_lines else None,
                    section=state.section,
                    item=item,
                    level=level,
                )
            )

        def flush() -> None:
            nonlocal current
            if current is not None:
                emit(current.kind, " ".join(current.parts), current.line, current.item)
            current = None

        i = 0
        while i < len(lines):
            raw = lines[i]
            lineno = i + 1
            stripped = raw.strip()

            fence = _FENCE.match(raw)
            if fence:
                flush()
                list_ids.clear()
                marker = fence.group(1)
                body: list[str] = []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith(marker):
                    body.append(lines[i].rstrip())
                    i += 1
                emit("paragraph", "\n".join(body), lineno + 1, None)
                i += 1  # закрывающая ограда
                continue

            if not stripped:
                flush()
                i += 1
                continue

            heading = _HEADING.match(raw)
            if heading:
                flush()
                list_ids.clear()
                level = len(heading.group(1))
                title = clean_inline(heading.group(2))
                if title:
                    state.open_heading(level, title)
                    emit("heading", title, lineno, None, level)
                i += 1
                continue

            if _HR.match(raw):
                flush()
                list_ids.clear()
                i += 1
                continue

            if _TABLE_ROW.match(raw):
                flush()
                list_ids.clear()
                i = self._table(lines, i, emit)
                continue

            bullet = _BULLET.match(raw)
            ordered = None if bullet else _ORDERED.match(raw)
            if bullet or ordered:
                flush()
                if bullet:
                    indent, content = len(bullet.group(1)), bullet.group(2)
                    item = self.match_item(content)
                else:
                    assert ordered is not None
                    indent, content = len(ordered.group(1)), ordered.group(3)
                    item = self.match_item(content) or self._cfg.ordered_item_label.format(
                        n=ordered.group(2)
                    )
                while list_ids and list_ids[-1][0] >= indent:
                    list_ids.pop()
                if item is None and list_ids:
                    item = list_ids[-1][1]
                list_ids.append((indent, item))
                current = _Open("list_item", [clean_inline(content)], lineno, item)
                i += 1
                continue

            content = _BLOCKQUOTE.sub("", raw)
            if current is not None and current.kind == "list_item":
                # Продолжение элемента списка (с отступом или «ленивое»).
                current.parts.append(clean_inline(content))
                i += 1
                continue

            item = self.match_item(content)
            if current is not None and item is not None:
                flush()  # строка с новым номером пункта начинает новый блок
            if current is None:
                list_ids.clear()
                current = _Open("paragraph", [], lineno, item)
            current.parts.append(clean_inline(content))
            i += 1

        flush()
        return out

    def _table(self, lines: list[str], start: int, emit) -> int:
        i = start
        rows: list[tuple[int, list[str]]] = []
        while i < len(lines) and _TABLE_ROW.match(lines[i]):
            if not _TABLE_SEP.match(lines[i]):
                inner = lines[i].strip()[1:-1]
                rows.append((i + 1, _CELL_SPLIT.split(inner)))
            i += 1
        has_header = start + 1 < len(lines) and bool(_TABLE_SEP.match(lines[start + 1]))
        for n, (lineno, cells) in enumerate(rows):
            cleaned = [clean_inline(c) for c in cells]
            text = " | ".join(c for c in cleaned if c)
            item = None if (has_header and n == 0) else self.table_item(cells[0])
            emit("table_row", text, lineno, item)
        return i
