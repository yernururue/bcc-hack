"""Оркестратор этапов (docs/02-trd.md, §5): ingest → extract → check_each → check_set → verify
→ questions → verdict → render. Ошибка одного LLM-этапа не роняет прогон: отчёт помечается неполным.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from reqcheck.config import criteria, lexicon, models_config, thresholds
from reqcheck.errors import BudgetExceededError, ReqCheckError
from reqcheck.formats import InputFile, OutputTarget
from reqcheck.ingest import ingest
from reqcheck.llm.base import LLMClient
from reqcheck.llm.openai_compat import LLMCallError, OpenAICompatClient
from reqcheck.schemas import (
    Debug,
    DocumentInfo,
    LLMUsage,
    Report,
    RunPassport,
    StageError,
)
from reqcheck.schemas.document import Document
from reqcheck.settings import Settings
from reqcheck.stages import check_each, check_set, extract, questions, rules, verdict, verify
from reqcheck.version import agent_version

Progress = Callable[[str], None]


@dataclass(frozen=True)
class Options:
    model: str
    max_cost_usd: float
    use_cache: bool = True
    dry_run: bool = False


STAGES = (
    "ingest",
    "extract",
    "check_each",
    "check_set",
    "verify",
    "questions",
    "verdict",
    "render",
)


class NotImplementedYet(NotImplementedError):
    """Возможность ещё не реализована."""


@dataclass
class RunOutcome:
    report: Report | None
    budget_exceeded: bool = False
    written: list = field(default_factory=list)


def _silent(_: str) -> None:
    pass


def estimate(doc: Document, model: str, batch_size: int) -> str:
    """Грубая оценка до прогона: ~3 символа на токен для русского текста."""
    blocks = extract.candidate_blocks(doc)
    doc_tokens = sum(len(b.text) + 40 for b in blocks) / 3
    reqs = max(1, round(len(blocks) * 0.5))
    calls = math.ceil(len(blocks) / extract.BLOCKS_PER_CALL) + math.ceil(reqs / batch_size) + 2
    tokens_in = doc_tokens * 2.5 + calls * 1500
    tokens_out = reqs * 250 + doc_tokens * 0.6
    info = models_config().get(model)
    price = (
        f"≈ ${(tokens_in * info.price_in + tokens_out * info.price_out) / 1e6:.3f}"
        if info
        else "цена модели неизвестна"
    )
    volume = f"~{tokens_in / 1000:.0f}k вход / ~{tokens_out / 1000:.0f}k выход"
    return f"Оценка:   ~{calls} вызовов, {volume}, {price}"


def build_client(settings: Settings, options: Options) -> OpenAICompatClient:
    assert settings.llm_api_key is not None
    return OpenAICompatClient(
        api_key=settings.llm_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=options.model,
        max_cost_usd=options.max_cost_usd,
        cache_dir=settings.cache_path,
        use_cache=options.use_cache,
        openrouter=settings.is_openrouter,
    )


def run(
    source: InputFile,
    target: OutputTarget,
    options: Options,
    settings: Settings,
    progress: Progress = _silent,
    llm: LLMClient | None = None,
) -> RunOutcome:
    started = datetime.now().astimezone()
    timings: dict[str, int] = {}
    errors: list[StageError] = []
    th, crit = thresholds(), criteria()

    def timed(stage: str, t0: float) -> None:
        timings[stage] = round((time.monotonic() - t0) * 1000)

    t0 = time.monotonic()
    doc = ingest(source)
    timed("ingest", t0)
    numbered = sum(1 for b in doc.blocks if b.anchor.item)
    pages = f", страниц: {doc.pages}" if doc.pages else ""
    progress(f"Разбор:   блоков {len(doc.blocks)}, с номером пункта {numbered}{pages}")
    progress(estimate(doc, options.model, th.batch_size))
    if options.dry_run:
        return RunOutcome(report=None)

    client = llm or build_client(settings, options)
    budget_hit = False

    def guarded[T](stage: str, fn: Callable[[], T], default: T) -> T:
        nonlocal budget_hit
        if budget_hit:
            errors.append(StageError(stage=stage, message="пропущен: исчерпан лимит стоимости"))
            return default
        t = time.monotonic()
        try:
            return fn()
        except BudgetExceededError as exc:
            budget_hit = True
            errors.append(StageError(stage=stage, message=exc.message))
            return default
        except LLMCallError as exc:
            errors.append(StageError(stage=stage, message=exc.message))
            return default
        finally:
            timed(stage, t)

    progress("Этап:     выделение требований")
    extracted = guarded(
        "extract",
        lambda: extract.run(doc, client, th.quote_min_similarity),
        extract.ExtractOutput([]),
    )
    reqs = extracted.requirements
    progress(f"          требований: {len(reqs)}")

    rule_findings = rules.run(reqs, lexicon(), th, crit)

    progress(
        f"Этап:     проверка каждого требования ({math.ceil(len(reqs) / th.batch_size)} батч.)"
    )
    judged = guarded(
        "check_each",
        lambda: check_each.run(reqs, client, crit, th.batch_size),
        check_each.CheckEachOutput(),
    )
    errors.extend(judged.errors)
    budget_hit = budget_hit or judged.budget_exceeded

    progress("Этап:     проверка всего набора")
    raw_set = guarded("check_set", lambda: check_set.run(reqs, client, crit), [])

    t = time.monotonic()
    per_req = verify.requirement_findings(reqs, judged, rule_findings, doc, th, crit)
    per_set = verify.set_findings(raw_set, reqs, doc, th, crit)
    kept, suppressed = verify.suppress_noise(
        per_req.findings + per_set.findings, th.findings_filter
    )
    findings = verify.number(kept, reqs)
    dropped = per_req.dropped + per_set.dropped
    reqs = verify.apply_assessments(reqs, judged, findings)
    timed("verify", t)
    progress(
        f"Проверка: в отчёте находок {len(findings)}, отброшено без цитаты {len(dropped)}, "
        f"отфильтровано как шум {len(suppressed)}"
    )

    progress("Этап:     вопросы к автору")
    known = {r.report_id for r in reqs}
    qs = guarded(
        "questions",
        lambda: questions.run(findings, known, client, th.report["max_questions"]),
        [],
    )

    summary = verdict.summarize(reqs, findings, extracted.split_composites)
    decision = verdict.decide(reqs, findings, th.verdict, th.report["max_top_fixes"])

    usage = getattr(client, "usage", None) or LLMUsage()
    incomplete = None
    if budget_hit:
        incomplete = f"исчерпан лимит стоимости ${options.max_cost_usd:.2f}"
    elif errors:
        incomplete = "часть этапов не выполнена: " + "; ".join(
            f"{e.stage}: {e.message}" for e in errors[:3]
        )

    report = Report(
        run=RunPassport(
            document=DocumentInfo(
                name=doc.source_name, sha256=doc.sha256, format=doc.format, pages=doc.pages
            ),
            started_at=started,
            model=client.model,
            provider_base_url=settings.llm_base_url,
            agent_version=agent_version(),
            llm=usage,
            complete=incomplete is None,
            incomplete_reason=incomplete,
        ),
        requirements=reqs,
        findings=findings,
        questions=qs,
        summary=summary,
        verdict=decision,
        debug=Debug(
            dropped_findings=dropped,
            suppressed_findings=suppressed,
            stage_errors=errors,
            timings_ms=timings,
        ),
    )

    from reqcheck import render

    t = time.monotonic()
    try:
        written = render.write(report, target)
    except OSError as exc:
        raise ReqCheckError(f"не удалось записать отчёт: {exc}") from exc
    timed("render", t)
    return RunOutcome(report=report, budget_exceeded=budget_hit, written=written)
