"""Отчёт — единственный источник правды; MD, PDF и UI строятся из него (docs/06-report-spec.md)."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from reqcheck.formats import InputFormat
from reqcheck.schemas.common import (
    Anchor,
    Assessment,
    Category,
    Characteristic,
    RequirementKind,
    Severity,
    Strict,
)

SCHEMA_VERSION = "1.0"


class Requirement(Strict):
    report_id: str = Field(pattern=r"^R-\d{3,}$")
    doc_id: str | None = None
    kind: RequirementKind
    summary: str
    quote: str
    anchor: Anchor
    split_from: str | None = None
    assessment: dict[Characteristic, Assessment] = Field(default_factory=dict)


class Finding(Strict):
    finding_id: str = Field(pattern=r"^F-\d{3,}$")
    scope: Literal["requirement", "set"]
    requirement_ids: list[str] = Field(default_factory=list)
    characteristic: Characteristic | None = None
    category: Category
    severity: Severity
    verdict: Literal["violation", "unclear"]
    quote: str
    anchor: Anchor
    related_anchors: list[Anchor] = Field(default_factory=list)
    explanation: str
    suggestion: str | None = None
    source: Literal["rule", "llm"]
    standard_ref: str | None = None
    verified: bool = False


class Question(Strict):
    id: str = Field(pattern=r"^Q-\d+$")
    question: str
    options: list[str] = Field(min_length=2, max_length=4)
    impacts: list[str] = Field(default_factory=list)
    default_assumption: str


class DocumentInfo(Strict):
    name: str
    sha256: str
    format: InputFormat
    pages: int | None = None


class LLMUsage(Strict):
    calls: int = 0
    cached_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class RunPassport(Strict):
    document: DocumentInfo
    started_at: datetime
    model: str
    provider_base_url: str
    agent_version: str
    llm: LLMUsage = Field(default_factory=LLMUsage)
    complete: bool = True
    incomplete_reason: str | None = None


class Critical(Strict):
    unverifiable: list[str] = Field(default_factory=list)
    contradictory: list[str] = Field(default_factory=list)
    incomplete: list[str] = Field(default_factory=list)


class Summary(Strict):
    requirements_total: int = 0
    split_composites: int = 0
    violations_total: int = 0
    unclear_total: int = 0
    by_severity: dict[Severity, int] = Field(default_factory=dict)
    critical: Critical = Field(default_factory=Critical)


class Verdict(Strict):
    can_start_development: bool
    can_start_testing: bool
    reason: str
    rule: str
    top_fixes: list[str] = Field(default_factory=list, max_length=5)


class StageError(Strict):
    stage: str
    message: str


class Debug(Strict):
    dropped_findings: list[Finding] = Field(default_factory=list)
    stage_errors: list[StageError] = Field(default_factory=list)
    timings_ms: dict[str, int] = Field(default_factory=dict)


class Report(Strict):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run: RunPassport
    requirements: list[Requirement] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list, max_length=10)
    summary: Summary = Field(default_factory=Summary)
    verdict: Verdict
    debug: Debug = Field(default_factory=Debug)
