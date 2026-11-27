"""Исходный PDF с пометками (ADR-009): выделение цитаты и комментарий находки на её месте.

Выделение строится по Anchor.rects (их заполняет stages/rects.py). Цвет — по серьёзности:
красный только у blocker (docs/05-design.md), жёлтый у major, серый у minor. Связанные места
(второе место противоречия или дубля) получают свою пометку со ссылкой на номер находки.
"""

from pathlib import Path
from typing import Any

from reqcheck.schemas import Anchor, Finding, Report, Severity

COLORS = {
    Severity.BLOCKER: (0.86, 0.15, 0.15),
    Severity.MAJOR: (1.0, 0.82, 0.2),
    Severity.MINOR: (0.75, 0.75, 0.75),
}
SEVERITY_RU = {
    Severity.BLOCKER: "блокирующая",
    Severity.MAJOR: "существенная",
    Severity.MINOR: "незначительная",
}


def _comment(f: Finding, related: bool) -> str:
    head = f"{f.finding_id} · {SEVERITY_RU[f.severity]} · {f.category.value}"
    reqs = ", ".join(f.requirement_ids)
    lines = [head + (f" · {reqs}" if reqs else "")]
    if related:
        lines.append(f"Второе место находки {f.finding_id}.")
    lines.append(f.explanation)
    if f.suggestion:
        lines.append(f"Как исправить: {f.suggestion}")
    return "\n".join(lines)


def _mark(doc: Any, anchor: Anchor, f: Finding, related: bool) -> int:
    import pymupdf

    by_page: dict[int, list[Any]] = {}
    for r in anchor.rects:
        by_page.setdefault(r.page, []).append(pymupdf.Rect(r.x0, r.y0, r.x1, r.y1))
    for page_no, rects in by_page.items():
        page = doc[page_no - 1]
        annot = page.add_highlight_annot(quads=[r.quad for r in rects])
        annot.set_colors(stroke=COLORS[f.severity])
        annot.set_info(content=_comment(f, related), title="ReqCheck", subject=f.finding_id)
        annot.update()
    return len(by_page)


def annotate_pdf(source: Path, report: Report, target: Path) -> int:
    """Пишет копию `source` с пометками; возвращает число поставленных пометок."""
    import pymupdf

    count = 0
    with pymupdf.open(source) as doc:
        for f in report.findings:
            count += _mark(doc, f.anchor, f, related=False)
            for a in f.related_anchors:
                count += _mark(doc, a, f, related=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(target), garbage=3, deflate=True)
    return count
