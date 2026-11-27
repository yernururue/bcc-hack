"""Этап check_set: проблемы набора — противоречия, пробелы, дубли, термины, трассируемость."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from reqcheck import prompts
from reqcheck.config import Criteria
from reqcheck.llm.base import LLMClient
from reqcheck.schemas import Requirement, Severity
from reqcheck.stages.check_each import format_requirement
from reqcheck.stages.empty import with_retry

SetCategory = Literal["contradiction", "missing_topic", "duplicate", "terminology", "traceability"]


class SetFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: SetCategory
    severity: Severity
    requirement_ids: list[str]
    quotes: list[str]
    explanation: str
    suggestion: str | None


class SetResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[SetFinding]


def run(requirements: list[Requirement], llm: LLMClient, criteria: Criteria) -> list[SetFinding]:
    if not requirements:
        return []
    user = prompts.render(
        "check_set",
        requirements="\n".join(format_requirement(r, criteria.kind_names) for r in requirements),
    )
    system = prompts.load("system")

    def ask(text: str) -> list[SetFinding]:
        return llm.complete(
            system=system, user=text, schema=SetResult, stage="check_set"
        ).value.findings

    return with_retry(ask, user, "check_set")
