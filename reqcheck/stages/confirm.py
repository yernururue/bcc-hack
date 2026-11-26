"""Этап confirm: подтверждение противоречий вторым коротким вызовом (ADR-006).

Находка «противоречие» меняет вердикт на «нельзя начинать», поэтому ложное стоит дорого.
Модель судит ту же пару цитат с текстами требований; опровергнутые уходят в debug.
Если пара не противоречива, но проблема есть (например, GET в методе, который меняет данные),
находка не теряется, а становится «некорректностью». При ошибке вызова находки остаются.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from reqcheck import prompts
from reqcheck.llm.base import LLMClient
from reqcheck.schemas import Category, Finding, Requirement, Severity


class ConfirmItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    verdict: Literal["conflict", "other_issue", "compatible"]
    reason: str


class ConfirmResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ConfirmItem]


def candidates(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.category == Category.CONTRADICTION]


def run(findings: list[Finding], reqs: list[Requirement], llm: LLMClient) -> dict[int, ConfirmItem]:
    """Решения модели по противоречиям, не подтверждённым как conflict: индекс → ответ."""
    pairs = [(i, f) for i, f in enumerate(findings) if f.category == Category.CONTRADICTION]
    if not pairs:
        return {}
    by_id = {r.report_id: r for r in reqs}
    lines: list[str] = []
    used: dict[str, Requirement] = {}
    for i, f in pairs:
        second = f.related_quotes[0] if f.related_quotes else "—"
        lines.append(f"C-{i} · A: {f.quote} · B: {second} · {f.explanation}")
        for rid in f.requirement_ids:
            if rid in by_id:
                used[rid] = by_id[rid]
    context = "\n".join(f"{r.report_id} · {r.anchor.label()} · {r.quote}" for r in used.values())
    user = prompts.render("confirm", candidates="\n".join(lines), context=context or "—")
    result = llm.complete(
        system=prompts.load("system"), user=user, schema=ConfirmResult, stage="confirm"
    ).value
    known = {f"C-{i}": i for i, _ in pairs}
    return {
        known[item.id]: item
        for item in result.items
        if item.id in known and item.verdict != "conflict"
    }


def as_other_issue(finding: Finding, reason: str, severity: Severity) -> Finding:
    """Не противоречие, но проблема есть: оставляем находку как «некорректность»."""
    return finding.model_copy(
        update={
            "category": Category.INCORRECT,
            "severity": severity,
            "explanation": reason.strip() or finding.explanation,
        }
    )
