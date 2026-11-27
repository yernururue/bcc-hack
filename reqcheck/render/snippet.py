"""Фрагмент оригинала для находки (ADR-009): PNG области страницы для PDF, абзацы для MD/TXT.

PDF: полоса страницы на всю ширину текста вокруг Anchor.rects, цитата подсвечена цветом
серьёзности (как в исходнике с пометками). Без rects — пусто: фрагмент «где-то на странице»
вводит в заблуждение сильнее, чем его отсутствие.
MD/TXT: блок с цитатой и по одному блоку до и после, с путём раздела.
"""

from pathlib import Path
from typing import Any

from reqcheck.render.annotate import COLORS
from reqcheck.schemas import Finding
from reqcheck.schemas.document import Document
from reqcheck.stages.extract import candidate_blocks
from reqcheck.stages.quotes import locate

MARGIN_Y = 28.0  # пунктов над и под цитатой
DPI = 110


def pdf_snippet(doc: Any, finding: Finding) -> bytes | None:
    """PNG по открытому pymupdf-документу; None, если координат цитаты нет."""
    import pymupdf

    rects = finding.anchor.rects
    if not rects:
        return None
    page_no = rects[0].page
    same = [r for r in rects if r.page == page_no]
    page = doc[page_no - 1]
    box = pymupdf.Rect(same[0].x0, same[0].y0, same[0].x1, same[0].y1)
    for r in same[1:]:
        box |= pymupdf.Rect(r.x0, r.y0, r.x1, r.y1)
    full = page.rect
    clip = pymupdf.Rect(
        full.x0 + 24, max(full.y0, box.y0 - MARGIN_Y), full.x1 - 24, min(full.y1, box.y1 + MARGIN_Y)
    )
    annot = page.add_highlight_annot(
        quads=[pymupdf.Rect(r.x0, r.y0, r.x1, r.y1).quad for r in same]
    )
    annot.set_colors(stroke=COLORS[finding.severity])
    annot.update()
    try:
        return page.get_pixmap(clip=clip, dpi=DPI).tobytes("png")
    finally:
        page.delete_annot(annot)


def pdf_snippets(pdf_path: Path, findings: list[Finding]) -> dict[str, bytes]:
    import pymupdf

    out: dict[str, bytes] = {}
    with pymupdf.open(pdf_path) as doc:
        for f in findings:
            png = pdf_snippet(doc, f)
            if png is not None:
                out[f.finding_id] = png
    return out


def text_snippet(doc: Document, finding: Finding, threshold: float) -> dict | None:
    """Абзацы вокруг цитаты: [{text, highlight, place}]; None, если цитату не нашли."""
    blocks = candidate_blocks(doc)
    block = locate(finding.quote, blocks, threshold) if finding.quote else None
    if block is None:
        return None
    i = blocks.index(block)
    around = blocks[max(0, i - 1) : i + 2]
    return {
        "finding_id": finding.finding_id,
        "quote": finding.quote,
        "paragraphs": [
            {"text": b.text, "highlight": b is block, "place": b.anchor.label()} for b in around
        ],
    }


def text_snippets(doc: Document, findings: list[Finding], threshold: float) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in findings:
        snip = text_snippet(doc, f, threshold)
        if snip is not None:
            out[f.finding_id] = snip
    return out
