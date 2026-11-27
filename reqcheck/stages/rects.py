"""Координаты цитат на страницах исходного PDF (ADR-009): Anchor.rects для подсветки.

Цитата уже найдена кодом в тексте блока (verify). Здесь ищем её слова на странице якоря:
последовательность слов страницы, совпадающая со словами цитаты по ключу сравнения. Так
переживаются переносы строк, кавычки и разметка, которую pymupdf4llm добавил в текст блока.
Прямоугольники — по одному на строку (объединение слов строки).
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from reqcheck.schemas import Anchor, Finding, Rect
from reqcheck.stages.quotes import match_key

MIN_WORDS = 2  # совсем короткую цитату («GET») подсвечивать ненадёжно: много совпадений


def _tokens(text: str) -> list[str]:
    return [t for t in match_key(text).replace("|", " ").split() if t.strip("-–—.,;:()")]


def _norm(word: str) -> str:
    return match_key(word).strip(".,;:()[]")


def find_rects(page: Any, quote: str) -> list[Rect]:
    """Прямоугольники слов цитаты на странице pymupdf; пусто, если не нашли."""
    want = [_norm(t) for t in _tokens(quote)]
    want = [w for w in want if w]
    if len(want) < MIN_WORDS:
        return []
    words = page.get_text("words")  # (x0, y0, x1, y1, word, block, line, wno)
    norm = [_norm(w[4]) for w in words]
    hit = _search(norm, want)
    if hit is None and len(want) >= 6:
        # цитата чуть отличается от текста (verify допускает сходство ≥ порога): берём отрезок
        # от первых трёх слов до последних трёх, если он не длиннее цитаты вдвое
        head, tail = _search(norm, want[:3]), _search(norm, want[-3:])
        if head and tail and head[0] < tail[1] <= head[0] + 2 * len(want):
            hit = (head[0], tail[1])
    if hit is None:
        return []
    start, end = hit
    lines: dict[tuple[int, int], list[float]] = {}
    for w in words[start:end]:
        key = (w[5], w[6])
        box = lines.setdefault(key, [w[0], w[1], w[2], w[3]])
        box[0], box[1] = min(box[0], w[0]), min(box[1], w[1])
        box[2], box[3] = max(box[2], w[2]), max(box[3], w[3])
    number = page.number + 1
    return [
        Rect(
            page=number, x0=round(b[0], 1), y0=round(b[1], 1), x1=round(b[2], 1), y1=round(b[3], 1)
        )
        for b in lines.values()
    ]


def _search(words: list[str], want: list[str]) -> tuple[int, int] | None:
    """Первое вхождение `want` в `words`; слово страницы может быть склеено с соседним
    (перенос «интернет-|магазина») — тогда сравниваем по склейке."""
    n = len(want)
    for i in range(len(words)):
        j, k = i, 0
        while k < n and j < len(words):
            w = words[j]
            if w == want[k]:
                j, k = j + 1, k + 1
                continue
            # слово страницы — начало склейки двух слов цитаты или наоборот
            if k + 1 < n and w == want[k] + want[k + 1]:
                j, k = j + 1, k + 2
                continue
            if j + 1 < len(words) and (w + words[j + 1]).replace("-", "") == want[k].replace(
                "-", ""
            ):
                j, k = j + 2, k + 1
                continue
            break
        if k == n:
            return i, j
    return None


def _anchor_rects(doc: Any, anchor: Anchor, quote: str) -> Anchor:
    pages: Iterable[int] = range(doc.page_count)
    if anchor.page is not None:
        # сначала страница якоря, затем соседние (цитата могла перейти на следующую)
        first = anchor.page - 1
        pages = [p for p in (first, first + 1, first - 1) if 0 <= p < doc.page_count]
    for p in pages:
        rects = find_rects(doc[p], quote)
        if rects:
            return anchor.model_copy(update={"rects": rects})
    return anchor


def attach_rects(findings: list[Finding], pdf_path: Path) -> list[Finding]:
    """Заполняет rects у якорей находок (основного и связанных) по цитатам."""
    import pymupdf

    out: list[Finding] = []
    with pymupdf.open(pdf_path) as doc:
        for f in findings:
            anchor = _anchor_rects(doc, f.anchor, f.quote) if f.quote else f.anchor
            related = [
                _anchor_rects(doc, a, q)
                for a, q in zip(f.related_anchors, f.related_quotes, strict=False)
            ]
            related += f.related_anchors[len(related) :]
            out.append(f.model_copy(update={"anchor": anchor, "related_anchors": related}))
    return out
