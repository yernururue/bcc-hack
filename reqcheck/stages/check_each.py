"""Этап check_each: LLM-судья оценивает каждое требование по 9 характеристикам (батчами)."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from reqcheck import prompts
from reqcheck.config import Criteria
from reqcheck.errors import AuthError, BudgetExceededError
from reqcheck.llm.base import LLMClient
from reqcheck.llm.openai_compat import LLMCallError
from reqcheck.schemas import Assessment, Characteristic, Requirement, Severity, StageError

MAX_PARALLEL = 4


class CharacteristicsAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unambiguous: Assessment
    complete: Assessment
    singular: Assessment
    feasible: Assessment
    verifiable: Assessment
    correct: Assessment
    necessary: Assessment
    implementation_free: Assessment
    conforming: Assessment


class JudgeFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    characteristic: Characteristic
    severity: Severity
    verdict: Literal["violation", "unclear"]
    quote: str
    explanation: str
    suggestion: str | None


class JudgeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str
    assessment: CharacteristicsAssessment
    findings: list[JudgeFinding]


class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JudgeItem]


@dataclass
class CheckEachOutput:
    items: dict[str, JudgeItem] = field(default_factory=dict)
    errors: list[StageError] = field(default_factory=list)
    budget_exceeded: bool = False


def format_criteria(criteria: Criteria) -> str:
    return "\n".join(
        f"- {code} ({info.name}): {info.definition}"
        for code, info in criteria.characteristics.items()
    )


def format_requirement(req: Requirement, kind_names: dict) -> str:
    text = req.quote.replace("\n", " ⏎ ")
    return f"{req.report_id} · {req.anchor.label()} · {kind_names.get(req.kind, req.kind)} · {text}"


def run(
    requirements: list[Requirement], llm: LLMClient, criteria: Criteria, batch_size: int
) -> CheckEachOutput:
    system = prompts.load("system")
    crit = format_criteria(criteria)
    batches = [requirements[i : i + batch_size] for i in range(0, len(requirements), batch_size)]
    out = CheckEachOutput()
    known = {r.report_id for r in requirements}

    def judge(batch: list[Requirement]) -> JudgeResult:
        user = prompts.render(
            "check_each",
            criteria=crit,
            requirements="\n".join(format_requirement(r, criteria.kind_names) for r in batch),
        )
        return llm.complete(system=system, user=user, schema=JudgeResult, stage="check_each").value

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        futures = [(batch, pool.submit(judge, batch)) for batch in batches]
        for batch, future in futures:
            try:
                result = future.result()
            except AuthError:
                raise
            except BudgetExceededError as exc:
                # Готовые батчи не теряем: отчёт будет помечен как неполный.
                out.budget_exceeded = True
                out.errors.append(StageError(stage="check_each", message=exc.message))
                continue
            except LLMCallError as exc:
                ids = f"{batch[0].report_id}…{batch[-1].report_id}"
                out.errors.append(StageError(stage="check_each", message=f"{ids}: {exc.message}"))
                continue
            for item in result.items:
                if item.report_id in known:
                    out.items[item.report_id] = item
    return out
