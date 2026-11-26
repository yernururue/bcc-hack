"""Этап confirm: подтверждение противоречий вторым коротким вызовом (ADR-006).

Находка «противоречие» меняет вердикт на «нельзя начинать», поэтому ложное стоит дорого.
Модель судит ту же пару цитат с текстами требований; опровергнутые уходят в debug.
При ошибке вызова находки остаются (этап не должен терять настоящие противоречия).
"""

from pydantic import BaseModel, ConfigDict

from reqcheck import prompts
from reqcheck.llm.base import LLMClient
from reqcheck.schemas import Category, Finding, Requirement


class ConfirmItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    conflict: bool
    reason: str


class ConfirmResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ConfirmItem]


def candidates(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.category == Category.CONTRADICTION]


def run(findings: list[Finding], reqs: list[Requirement], llm: LLMClient) -> set[int]:
    """Индексы (в списке `findings`) противоречий, которые модель опровергла."""
    pairs = [(i, f) for i, f in enumerate(findings) if f.category == Category.CONTRADICTION]
    if not pairs:
        return set()
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
    refuted: set[int] = set()
    known = {f"C-{i}": i for i, _ in pairs}
    for item in result.items:
        if item.id in known and not item.conflict:
            refuted.add(known[item.id])
    return refuted
