"""Этап render: Report → MD / JSON (PDF — следующий этап плана)."""

import re
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined

from reqcheck.config import Criteria, criteria
from reqcheck.formats import OutputTarget
from reqcheck.schemas import Assessment, Characteristic, Finding, Report

MARKS = {Assessment.OK: "✓", Assessment.VIOLATION: "✗", Assessment.UNCLEAR: "?"}
CHAR_ORDER = [
    Characteristic.UNAMBIGUOUS,
    Characteristic.COMPLETE,
    Characteristic.SINGULAR,
    Characteristic.FEASIBLE,
    Characteristic.VERIFIABLE,
    Characteristic.CORRECT,
    Characteristic.NECESSARY,
    Characteristic.IMPLEMENTATION_FREE,
    Characteristic.CONFORMING,
]

_env = Environment(
    loader=PackageLoader("reqcheck.render", "templates"),
    autoescape=False,  # Markdown, не HTML
    undefined=StrictUndefined,
    trim_blocks=False,
    keep_trailing_newline=True,
)


def _inline(text: str) -> str:
    return " ".join(text.split())


def _cell(text: str) -> str:
    return _inline(text).replace("|", "\\|")


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def render_markdown(report: Report, crit: Criteria | None = None) -> str:
    crit = crit or criteria()
    by_req: dict[str, list[Finding]] = {}
    for f in report.findings:
        if f.scope == "requirement":
            by_req.setdefault(f.requirement_ids[0], []).append(f)
    set_findings = [f for f in report.findings if f.scope == "set"]
    set_groups = [
        (s.title, [f for f in set_findings if f.category in s.categories])
        for s in crit.set_sections
    ]
    by_id = {f.finding_id: f for f in report.findings}
    req_ids = {r.report_id: r for r in report.requirements}

    def label_ref(rid: str) -> str:
        r = req_ids.get(rid)
        return f"{rid} ({r.doc_id})" if r and r.doc_id else rid

    def places(f: Finding) -> str:
        anchors = [f.anchor, *f.related_anchors]
        return " ↔ ".join(a.label() for a in anchors) + (
            f" · {', '.join(label_ref(i) for i in f.requirement_ids)}" if f.requirement_ids else ""
        )

    def quotes(f: Finding) -> str:
        return " ↔ ".join(f"«{_inline(q)}»" for q in [f.quote, *f.related_quotes])

    def ok(r) -> bool:
        return bool(r.assessment) and all(v == Assessment.OK for v in r.assessment.values())

    started = report.run.started_at
    offset = started.strftime("%z")
    tz = f" (UTC{offset[:3]}:{offset[3:]})" if offset else ""
    v = report.verdict
    dev = v.development or ("можно начинать" if v.can_start_development else "нельзя начинать")
    test = v.testing or (
        "можно начинать тест-дизайн" if v.can_start_testing else "нельзя начинать тест-дизайн"
    )

    text = _env.get_template("report.md.j2").render(
        r=report,
        crit=crit,
        chars=CHAR_ORDER,
        by_req=by_req,
        by_id=by_id,
        set_groups=set_groups,
        started=started.strftime("%Y-%m-%d %H:%M") + tz,
        provider="OpenRouter"
        if "openrouter.ai" in report.run.provider_base_url
        else (f"`{report.run.provider_base_url}`"),
        dev=dev,
        test=test,
        kind=lambda k: crit.kind_names.get(k, str(k)),
        cat=lambda c: crit.category_names.get(c, str(c)),
        mark=lambda a: MARKS.get(a, "·") if a else "·",
        cell=_cell,
        inline=_inline,
        num=_num,
        ok=ok,
        places=places,
        quotes=quotes,
        refs=lambda ids: ", ".join(ids) if ids else "нет",
        options=lambda opts: "; ".join(f"{chr(0x430 + i)}) {o}" for i, o in enumerate(opts)),
        impacts=lambda ids: ", ".join(label_ref(i) for i in ids) if ids else "—",
    )
    text = re.sub(r"[ \t]+\n", "\n", text)  # хвостовые пробелы
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def write(report: Report, target: OutputTarget) -> list[Path]:
    """Пишет отчёт в формате target и JSON рядом. Возвращает записанные пути."""
    target.path.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if target.format == "md":
        target.path.write_text(render_markdown(report), encoding="utf-8")
        written.append(target.path)
    elif target.format == "pdf":
        raise NotImplementedError("PDF-отчёт ещё не реализован")
    json_text = report.model_dump_json(indent=2)
    target.json_path.parent.mkdir(parents=True, exist_ok=True)
    target.json_path.write_text(json_text, encoding="utf-8")
    if target.json_path != target.path:
        written.append(target.json_path)
    return written
