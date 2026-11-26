"""Данные для шаблонов отчёта: одни и те же для MD и PDF, чтобы содержимое совпадало."""

from typing import Any

from reqcheck.config import Criteria
from reqcheck.schemas import Assessment, Characteristic, Finding, Report, Requirement

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


def inline(text: str) -> str:
    return " ".join(text.split())


def num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def build(report: Report, crit: Criteria) -> dict[str, Any]:
    by_req: dict[str, list[Finding]] = {}
    for f in report.findings:
        if f.scope == "requirement":
            by_req.setdefault(f.requirement_ids[0], []).append(f)
    set_findings = [f for f in report.findings if f.scope == "set"]
    set_groups = [
        (s.title, [f for f in set_findings if f.category in s.categories])
        for s in crit.set_sections
    ]
    req_ids = {r.report_id: r for r in report.requirements}

    def label_ref(rid: str) -> str:
        r = req_ids.get(rid)
        return f"{rid} ({r.doc_id})" if r and r.doc_id else rid

    def ok(r: Requirement) -> bool:
        return bool(r.assessment) and all(v == Assessment.OK for v in r.assessment.values())

    started = report.run.started_at
    offset = started.strftime("%z")
    tz = f" (UTC{offset[:3]}:{offset[3:]})" if offset else ""
    v = report.verdict
    base = report.run.provider_base_url
    return {
        "r": report,
        "crit": crit,
        "chars": CHAR_ORDER,
        "by_req": by_req,
        "by_id": {f.finding_id: f for f in report.findings},
        "set_groups": set_groups,
        "no_id": [q for q in report.requirements if q.doc_id is None],
        "started": started.strftime("%Y-%m-%d %H:%M") + tz,
        "provider": "OpenRouter" if "openrouter.ai" in base else base,
        "dev": v.development
        or ("можно начинать" if v.can_start_development else "нельзя начинать"),
        "test": v.testing
        or ("можно начинать тест-дизайн" if v.can_start_testing else "нельзя начинать тест-дизайн"),
        "kind": lambda k: crit.kind_names.get(k, str(k)),
        "cat": lambda c: crit.category_names.get(c, str(c)),
        "mark": lambda a: MARKS.get(a, "·") if a else "·",
        "inline": inline,
        "num": num,
        "ok": ok,
        "label_ref": label_ref,
        "places": lambda f: [a.label() for a in [f.anchor, *f.related_anchors]],
        "quotes": lambda f: [inline(q) for q in [f.quote, *f.related_quotes]],
        "letter": lambda i: chr(0x430 + i),  # а, б, в, г
    }
