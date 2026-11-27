"""Этап questions: вопросы к автору документа по подтверждённым находкам (≤ 10)."""

from pydantic import BaseModel, ConfigDict

from reqcheck import prompts
from reqcheck.llm.base import LLMClient
from reqcheck.schemas import Finding, Question, Requirement
from reqcheck.stages.verify import SEVERITY_RANK

MAX_FINDINGS_IN_PROMPT = 40


class QuestionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    options: list[str]
    impacts: list[str]
    default_assumption: str


class QuestionsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[QuestionOut]


def run(
    findings: list[Finding],
    known_ids: set[str],
    llm: LLMClient,
    max_questions: int,
    requirements: list[Requirement] | None = None,
) -> list[Question]:
    important = sorted(
        (f for f in findings if f.severity != "minor" or f.verdict == "unclear"),
        key=lambda f: SEVERITY_RANK[f.severity],
    )[:MAX_FINDINGS_IN_PROMPT]
    if not important:
        return []
    lines = [
        f"{f.finding_id} · {f.severity} · {', '.join(f.requirement_ids) or '—'} · "
        f"{f.explanation} Цитата: «{f.quote}»"
        for f in important
    ]
    reqs = "\n".join(
        f"{r.report_id} · {r.quote.replace(chr(10), ' ⏎ ')}" for r in requirements or []
    )
    user = prompts.render(
        "questions",
        max_questions=str(max_questions),
        findings="\n".join(lines),
        requirements=reqs or "—",
    )
    system = prompts.load("system")
    raw = llm.complete(system=system, user=user, schema=QuestionsResult, stage="questions")
    out: list[Question] = []
    for q in raw.value.questions:
        options = [o.strip() for o in q.options if o.strip()][:4]
        if len(options) < 2 or not q.question.strip():
            continue
        out.append(
            Question(
                id=f"Q-{len(out) + 1}",
                question=q.question.strip(),
                options=options,
                impacts=[i for i in dict.fromkeys(q.impacts) if i in known_ids],
                default_assumption=q.default_assumption.strip(),
            )
        )
        if len(out) == max_questions:
            break
    return out
