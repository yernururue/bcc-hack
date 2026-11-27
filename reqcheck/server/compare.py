"""Режим сравнения для демонстрации: конвейер против одного промпта на том же документе.

Один промпт — тот же baseline, что в eval (reqcheck/eval/baseline.py): та же схема находок и та
же проверка цитат кодом, поэтому сравнение честное. Совпадение находок считаем по цитатам, как
eval сопоставляет находки с разметкой (quote_overlap и порог из thresholds.yaml, eval). Цифры
считает код; на вердикт и отчёт конвейера сравнение не влияет.
"""

import time

from reqcheck.config import Criteria, thresholds
from reqcheck.eval import baseline
from reqcheck.eval.matching import EvalFinding, quote_overlap
from reqcheck.llm.base import LLMClient
from reqcheck.schemas import Finding, Report, Severity
from reqcheck.schemas.document import Document

SEVERITY_NAMES = {"blocker": "блокирующий", "major": "существенный", "minor": "незначительный"}
ORDER = {Severity.BLOCKER.value: 0, Severity.MAJOR.value: 1, Severity.MINOR.value: 2}
TOP = 5


def _quotes(f: Finding) -> list[str]:
    return [q for q in [f.quote, *f.related_quotes] if q]


def _same(a: list[str], b: list[str], threshold: float, min_chars: int) -> bool:
    return any(quote_overlap(x, y, min_chars) >= threshold for x in a for y in b)


def _counts(findings: list, severity) -> dict:
    sev = [severity(f) for f in findings]
    return {
        "findings": len(findings),
        "blockers": sev.count(Severity.BLOCKER.value),
        "major": sev.count(Severity.MAJOR.value),
    }


def compare(report: Report, doc: Document, llm: LLMClient, crit: Criteria) -> dict:
    th = thresholds()
    overlap, min_chars = th.eval.quote_overlap, th.eval.min_quote_chars
    cost_before = llm.usage.cost_usd
    t0 = time.monotonic()
    out = baseline.run(doc, llm, crit, th.quote_min_similarity)
    seconds = round(time.monotonic() - t0, 1)

    pipe_quotes = [_quotes(f) for f in report.findings]
    base_in_pipe = [
        any(_same(b.quotes, p, overlap, min_chars) for p in pipe_quotes) for b in out.findings
    ]
    pipe_in_base = [
        any(_same(b.quotes, p, overlap, min_chars) for b in out.findings) for p in pipe_quotes
    ]

    def top(findings: list[EvalFinding], flags: list[bool]) -> list[dict]:
        rows = sorted(zip(findings, flags, strict=True), key=lambda x: ORDER[x[0].severity])
        return [
            {
                "category": crit.category_names.get(f.category, f.category),  # type: ignore[call-overload]
                "severity": f.severity,
                "severity_name": SEVERITY_NAMES[f.severity],
                "quote": f.quotes[0],
                "explanation": f.explanation,
                "in_pipeline": flag,
            }
            for f, flag in rows[:TOP]
        ]

    timings = report.debug.timings_ms
    return {
        "pipeline": {
            **_counts(report.findings, lambda f: f.severity.value),
            "dropped": len(report.debug.dropped_findings),
            "cost_usd": round(report.run.llm.cost_usd, 4),
            "seconds": round(sum(timings.values()) / 1000, 1) if timings else None,
        },
        "baseline": {
            **_counts(out.findings, lambda f: f.severity),
            "dropped": out.dropped,
            "cost_usd": round(llm.usage.cost_usd - cost_before, 4),
            "seconds": seconds,
        },
        "overlap": {
            "both": sum(pipe_in_base),
            "only_pipeline": pipe_in_base.count(False),
            "only_baseline": base_in_pipe.count(False),
        },
        "baseline_top": top(out.findings, base_in_pipe),
    }
