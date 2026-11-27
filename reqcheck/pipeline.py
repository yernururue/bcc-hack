"""Оркестратор этапов (docs/02-trd.md, §5): ingest → extract → check_each → check_set → verify
→ questions → verdict → render. Ошибка одного LLM-этапа не роняет прогон: отчёт помечается неполным.
"""

import logging
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
    Category,
    Debug,
    DocumentInfo,
    Finding,
    LLMUsage,
    Report,
    RunPassport,
    StageError,
)
from reqcheck.schemas.document import Document
from reqcheck.settings import Settings
from reqcheck.stages import (
    check_each,
    check_set,
    confirm,
    extract,
    questions,
    rules,
    verdict,
    verify,
)
from reqcheck.version import agent_version

Progress = Callable[[str], None]
# (этап, состояние active|done|error, пояснение при ошибке)
StageEvent = Callable[[str, str, str | None], None]


@dataclass(frozen=True)
class Options:
    model: str
    max_cost_usd: float
    use_cache: bool = True
    dry_run: bool = False
    cache_salt: str = ""  # только для повторов eval: отдельные ответы в кэше на каждый повтор
    ocr: bool = False  # распознавать PDF без текстового слоя (нужен Tesseract)


# Этапы для индикатора прогресса (UI показывает ровно их, по порядку). Подтверждение
# противоречий входит в check_set.
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


@dataclass(frozen=True)
class Estimate:
    calls: int
    tokens_in: float
    tokens_out: float
    usd: float | None  # None, если цена модели неизвестна


def _price(model: str, tokens_in: float, tokens_out: float) -> float | None:
    info = models_config().get(model)
    if info is None:
        return None
    return (tokens_in * info.price_in + tokens_out * info.price_out) / 1e6


def _factor(name: str) -> float:
    """Поправка оценки на факт (config/thresholds.yaml, estimate); 1.0, если не задана."""
    return float(thresholds().estimate.get(name, 1.0))


def estimate_numbers(doc: Document, model: str, batch_size: int) -> Estimate:
    """Грубая оценка до прогона: ~3 символа на токен для русского текста."""
    blocks = extract.candidate_blocks(doc)
    doc_tokens = sum(len(b.text) + 40 for b in blocks) / 3
    reqs = max(1, round(len(blocks) * 0.5))
    calls = math.ceil(len(blocks) / extract.BLOCKS_PER_CALL) + math.ceil(reqs / batch_size) + 2
    k = _factor("pipeline_factor")
    tokens_in = (doc_tokens * 2.5 + calls * 1500) * k
    tokens_out = (reqs * 250 + doc_tokens * 0.6) * k
    return Estimate(calls, tokens_in, tokens_out, _price(model, tokens_in, tokens_out))


def estimate_baseline(doc: Document, model: str) -> Estimate:
    """Один вызов: весь документ плюс критерии на входе, список находок на выходе."""
    blocks = extract.candidate_blocks(doc)
    doc_tokens = sum(len(b.text) + 40 for b in blocks) / 3
    k = _factor("baseline_factor")
    tokens_in = (doc_tokens + 2500) * k
    tokens_out = (1500 + len(blocks) * 40) * k
    return Estimate(1, tokens_in, tokens_out, _price(model, tokens_in, tokens_out))


def estimate(doc: Document, model: str, batch_size: int) -> str:
    e = estimate_numbers(doc, model, batch_size)
    price = f"≈ ${e.usd:.3f}" if e.usd is not None else "цена модели неизвестна"
    volume = f"~{e.tokens_in / 1000:.0f}k вход / ~{e.tokens_out / 1000:.0f}k выход"
    return f"Оценка:   ~{e.calls} вызовов, {volume}, {price}"


def _with_rects(findings: list[Finding], source: InputFile) -> list[Finding]:
    """Координаты цитат в исходном PDF (ADR-009); без них отчёт тот же, только без подсветки."""
    from reqcheck.stages.rects import attach_rects

    try:
        return attach_rects(findings, source.path)
    except Exception:  # повреждённая страница не должна ронять проверку
        logging.getLogger(__name__).warning("не удалось найти координаты цитат", exc_info=True)
        return findings


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
        cache_salt=options.cache_salt,
    )


def run(
    source: InputFile,
    target: OutputTarget,
    options: Options,
    settings: Settings,
    progress: Progress = _silent,
    llm: LLMClient | None = None,
    on_stage: StageEvent | None = None,
) -> RunOutcome:
    started = datetime.now().astimezone()
    timings: dict[str, int] = {}
    errors: list[StageError] = []
    th, crit = thresholds(), criteria()

    def timed(stage: str, t0: float) -> None:
        timings[stage] = round((time.monotonic() - t0) * 1000)

    def begin(stage: str) -> None:
        if on_stage:
            on_stage(stage, "active", None)

    def end(*stage_names: str) -> None:
        """Этап завершён; ошибка, если по нему (или входящему в него этапу) есть StageError."""
        if on_stage is None:
            return
        stage = stage_names[0]
        failed = next((e for e in errors if e.stage in stage_names), None)
        on_stage(stage, "error" if failed else "done", failed.message if failed else None)

    t0 = time.monotonic()
    begin("ingest")
    doc = ingest(source, ocr=options.ocr)
    timed("ingest", t0)
    end("ingest")
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
    begin("extract")
    extracted = guarded(
        "extract",
        lambda: extract.run(doc, client, th.quote_min_similarity),
        extract.ExtractOutput([]),
    )
    reqs = extracted.requirements
    end("extract")
    progress(f"          требований: {len(reqs)}")

    rule_findings = rules.run(reqs, lexicon(), th, crit)

    progress(
        f"Этап:     проверка каждого требования ({math.ceil(len(reqs) / th.batch_size)} батч.)"
    )
    begin("check_each")
    judged = guarded(
        "check_each",
        lambda: check_each.run(reqs, client, crit, th.batch_size),
        check_each.CheckEachOutput(),
    )
    errors.extend(judged.errors)
    budget_hit = budget_hit or judged.budget_exceeded
    end("check_each")

    progress("Этап:     проверка всего набора")
    begin("check_set")
    raw_set = guarded("check_set", lambda: check_set.run(reqs, client, crit), [])

    t = time.monotonic()
    per_req = verify.requirement_findings(reqs, judged, rule_findings, doc, th, crit)
    per_set = verify.set_findings(raw_set, reqs, doc, th, crit)
    timed("verify", t)

    decisions: dict[int, confirm.ConfirmItem] = {}
    if confirm.candidates(per_set.findings):
        progress("Этап:     подтверждение противоречий")
        decisions = guarded(
            "confirm",
            lambda: confirm.run(per_set.findings, reqs, client),
            dict[int, confirm.ConfirmItem](),
        )
    end("check_set", "confirm")
    begin("verify")
    refuted: list[Finding] = []
    set_kept: list[Finding] = []
    for i, f in enumerate(per_set.findings):
        item = decisions.get(i)
        if item is None:
            set_kept.append(f)
        elif item.verdict == "other_issue":
            converted = confirm.as_other_issue(
                f, item.reason, th.default_severity[Category.INCORRECT]
            )
            # то же требование уже отмечено как некорректное судьёй: второй раз не повторяем
            seen = any(
                g.category == Category.INCORRECT and set(g.requirement_ids) & set(f.requirement_ids)
                for g in per_req.findings
            )
            (refuted if seen else set_kept).append(converted if not seen else f)
        else:
            refuted.append(f)

    t = time.monotonic()
    kept, suppressed = verify.suppress_noise(per_req.findings + set_kept, th.findings_filter)
    suppressed += refuted
    findings = verify.number(kept, reqs)
    if doc.format == "pdf":
        findings = _with_rects(findings, source)
    dropped = per_req.dropped + per_set.dropped
    reqs = verify.apply_assessments(reqs, judged, findings)
    timings["verify"] = timings.get("verify", 0) + round((time.monotonic() - t) * 1000)
    progress(
        f"Проверка: в отчёте находок {len(findings)}, отброшено без цитаты {len(dropped)}, "
        f"отфильтровано как шум {len(suppressed)}"
    )

    end("verify")
    progress("Этап:     вопросы к автору")
    begin("questions")
    known = {r.report_id for r in reqs}
    qs = guarded(
        "questions",
        lambda: questions.run(findings, known, client, th.report["max_questions"]),
        [],
    )
    end("questions")

    begin("verdict")
    summary = verdict.summarize(reqs, findings, extracted.split_composites)
    decision = verdict.decide(reqs, findings, th.verdict, th.report["max_top_fixes"])

    usage = getattr(client, "usage", None) or LLMUsage()
    incomplete = None
    if budget_hit:
        # причина в сообщении этапа: наш лимит или отказ провайдера (402)
        incomplete = next(
            (e.message for e in errors if not e.message.startswith("пропущен")),
            f"исчерпан лимит стоимости ${options.max_cost_usd:.2f}",
        )
    elif errors:
        incomplete = "часть этапов не выполнена: " + "; ".join(
            f"{e.stage}: {e.message}" for e in errors[:3]
        )
    end("verdict")

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
    begin("render")
    try:
        written = render.write(report, target)
    except OSError as exc:
        raise ReqCheckError(f"не удалось записать отчёт: {exc}") from exc
    timed("render", t)
    end("render")
    return RunOutcome(report=report, budget_exceeded=budget_hit, written=written)
