"""Представление результата для экрана S3: тексты и цифры готовит код, а не интерфейс."""

from reqcheck.config import Criteria
from reqcheck.schemas import Finding, Report

SEVERITY_NAMES = {"blocker": "блокирующий", "major": "существенный", "minor": "незначительный"}


def plural(n: int, one: str, few: str, many: str) -> str:
    m10, m100 = n % 10, n % 100
    if m10 == 1 and m100 != 11:
        return one
    if 2 <= m10 <= 4 and not 12 <= m100 <= 14:
        return few
    return many


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def _title(f: Finding, report: Report, crit: Criteria) -> str:
    by_id = {r.report_id: r for r in report.requirements}
    names = [by_id[rid].doc_id or rid for rid in f.requirement_ids if rid in by_id]
    head = crit.category_names.get(f.category, f.category.value)
    return f"{head} · {', '.join(names)}" if names else head


def _anchor(f: Finding) -> str:
    return " ↔ ".join([f.anchor.label(), *(a.label() for a in f.related_anchors)])


def build_view(report: Report, crit: Criteria) -> dict:
    verdict, summary = report.verdict, report.summary
    blockers = summary.by_severity.get("blocker", 0)
    if verdict.can_start_development:
        headline = "Можно начинать разработку"
        # «с оговорками» и состояние тестирования — в подзаголовке
        sub = _capitalize(verdict.development) + ". " + _capitalize(verdict.testing) + "."
        if verdict.development == "можно начинать":
            sub = _capitalize(verdict.testing) + "."
    else:
        word = plural(blockers, "блокирующий дефект", "блокирующих дефекта", "блокирующих дефектов")
        headline = f"Нельзя начинать: {blockers} {word}"
        sub = _capitalize(verdict.testing) + "."

    by_finding = {f.finding_id: f for f in report.findings}
    fixes = []
    for fid in verdict.top_fixes:
        f = by_finding.get(fid)
        if f is None:
            continue
        fixes.append(
            {
                "id": f.finding_id,
                "severity": f.severity.value,
                "severity_name": SEVERITY_NAMES[f.severity.value],
                "title": _title(f, report, crit),
                "anchor": _anchor(f),
                "quotes": [f.quote, *f.related_quotes],
                "explanation": f.explanation,
                "suggestion": f.suggestion,
            }
        )
    return {
        "can_start_development": verdict.can_start_development,
        "can_start_testing": verdict.can_start_testing,
        "headline": headline,
        "sub": sub,
        "stats": {
            "requirements": summary.requirements_total,
            "violations": summary.violations_total,
            "blockers": blockers,
        },
        "fixes": fixes,
        "complete": report.run.complete,
        "incomplete_reason": report.run.incomplete_reason,
        "model": report.run.model,
        "cost_usd": round(report.run.llm.cost_usd, 4),
    }
