"""Baseline: один промпт «найди все дефекты во всём документе» без этапов и правил.

Схема находок и проверка цитат кодом те же, что в конвейере: так сравнение честное.
"""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from reqcheck import prompts
from reqcheck.config import Criteria
from reqcheck.eval.matching import EvalFinding
from reqcheck.llm.base import LLMClient
from reqcheck.schemas import Category, Severity
from reqcheck.schemas.document import Document
from reqcheck.stages.check_each import format_criteria
from reqcheck.stages.extract import candidate_blocks, format_block
from reqcheck.stages.quotes import locate

# Категории, для которых нужны минимум два места (как в stages/verify.py).
NEEDS_TWO = {Category.CONTRADICTION.value, Category.DUPLICATE.value}


class BaselineFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    severity: Severity
    quotes: list[str]
    explanation: str
    suggestion: str | None


class BaselineResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[BaselineFinding]


@dataclass
class BaselineOutput:
    findings: list[EvalFinding]
    raw: int  # сколько находок вернула модель
    dropped: int  # сколько отброшено: цитаты не найдены в тексте


def run(doc: Document, llm: LLMClient, criteria: Criteria, threshold: float) -> BaselineOutput:
    blocks = candidate_blocks(doc)
    user = prompts.render(
        "baseline",
        criteria=format_criteria(criteria),
        blocks="\n".join(format_block(b) for b in blocks),
    )
    result = llm.complete(
        system=prompts.load("system"), user=user, schema=BaselineResult, stage="baseline"
    ).value

    kept: list[EvalFinding] = []
    for f in result.findings:
        placed: list[str] = []
        seen: set[str] = set()
        for quote in f.quotes:
            block = locate(quote, blocks, threshold)
            if block is not None and block.block_id not in seen:
                seen.add(block.block_id)
                placed.append(quote.strip())
        need = 2 if f.category.value in NEEDS_TWO else 1
        if len(placed) >= need:
            kept.append(
                EvalFinding(
                    category=f.category.value,
                    severity=f.severity.value,
                    quotes=placed,
                    explanation=f.explanation.strip(),
                )
            )
    return BaselineOutput(kept, raw=len(result.findings), dropped=len(result.findings) - len(kept))
