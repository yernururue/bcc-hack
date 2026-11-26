"""Прогон модели по фикстурам: конвейер или baseline → находки → метрики, стоимость, время."""

import re
import tempfile
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field

from reqcheck import pipeline
from reqcheck.config import criteria, models_config, thresholds
from reqcheck.errors import BudgetExceededError
from reqcheck.eval import baseline
from reqcheck.eval.matching import (
    EvalFinding,
    ExpectedFile,
    FixtureScore,
    Metrics,
    compute_metrics,
    fixture_path,
    score_fixture,
)
from reqcheck.formats import detect_input, resolve_output
from reqcheck.ingest import ingest
from reqcheck.llm.base import LLMClient
from reqcheck.llm.openai_compat import LLMCallError
from reqcheck.schemas.common import Strict
from reqcheck.settings import Settings
from reqcheck.stages.extract import candidate_blocks
from reqcheck.version import agent_version

Mode = Literal["pipeline", "baseline", "hybrid"]
Log = Callable[[str], None]


class FixtureRun(Strict):
    score: FixtureScore
    blocks: int = 0  # блоков-кандидатов в документе (размер для маршрутизации)
    routed: Literal["pipeline", "baseline"] | None = None  # для hybrid: чем проверен документ
    cost_usd: float = 0.0
    seconds: float = 0.0
    calls: int = 0
    cached_calls: int = 0
    complete: bool = True
    note: str | None = None
    dropped_unverified: int = 0  # находки без проверенной цитаты (не попали в отчёт)


class EvalResult(Strict):
    date: str
    model: str
    mode: Mode
    agent_version: str
    repeat: int = 1  # номер повтора (1 — обычный кэш, 2+ — с солью ключа)
    variant: str = ""  # для hybrid: порог маршрутизации, например «≤40 блоков»
    fixtures: list[FixtureRun] = Field(default_factory=list)
    metrics: Metrics
    metrics_by_split: dict[str, Metrics] = Field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        return sum(f.cost_usd for f in self.fixtures)

    @property
    def seconds(self) -> float:
        return sum(f.seconds for f in self.fixtures)

    @property
    def cached(self) -> bool:
        return any(f.cached_calls for f in self.fixtures)


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", model.lower()).strip("-")


def result_path(out_dir: Path, result: EvalResult, tag: str = "") -> Path:
    suffix = "" if result.mode == "pipeline" else f"-{result.mode}"
    rep = f"-r{result.repeat}" if result.repeat > 1 else ""
    tag = f"-{tag}" if tag else ""
    return out_dir / f"{result.date}-{slug(result.model)}{suffix}{tag}{rep}.json"


def split_metrics(runs: list[FixtureRun]) -> dict[str, Metrics]:
    splits = sorted({r.score.split for r in runs})
    return {s: compute_metrics([r.score for r in runs if r.score.split == s]) for s in splits}


def salt(repeat: int) -> str:
    """Повтор 1 — обычный кэш (тот же ответ, что у CLI); повторы 2+ — свои ключи в кэше."""
    return "" if repeat <= 1 else f"eval-repeat-{repeat}"


def finish(model: str, mode: Mode, runs: list[FixtureRun], repeat: int = 1) -> EvalResult:
    return EvalResult(
        date=date.today().isoformat(),
        model=model,
        mode=mode,
        agent_version=agent_version(),
        repeat=repeat,
        fixtures=runs,
        metrics=compute_metrics([r.score for r in runs]),
        metrics_by_split=split_metrics(runs),
    )


def hybrid(pipe: EvalResult, base: EvalResult, max_blocks: int) -> EvalResult:
    """Маршрутизация по размеру: документ до `max_blocks` блоков — baseline, больше — конвейер.

    Считается из уже готовых прогонов того же повтора: новых вызовов модели нет.
    """
    by_name = {r.score.fixture: r for r in base.fixtures}
    runs = []
    for p in pipe.fixtures:
        b = by_name[p.score.fixture]
        use_base = p.blocks <= max_blocks
        chosen = b if use_base else p
        runs.append(chosen.model_copy(update={"routed": "baseline" if use_base else "pipeline"}))
    out = finish(pipe.model, "hybrid", runs, pipe.repeat)
    out.variant = f"≤{max_blocks} блоков → baseline"
    return out


def _sections(anchors) -> list[str]:
    out: list[str] = []
    for a in anchors:
        out.extend(a.section_path)
        if a.item:
            out.append(a.item)
    return list(dict.fromkeys(out))


def check_tier(model: str, allow_strong: bool) -> None:
    """Деньги владельца: сильные и неизвестные модели — только с явным флагом."""
    info = models_config().get(model)
    if info is None or info.tier not in models_config().test_tiers:
        tier = info.tier if info else "неизвестен"
        if not allow_strong:
            raise ValueError(
                f"модель {model} (tier: {tier}) не из {models_config().test_tiers}: "
                "нужно явное согласие владельца (--allow-strong)"
            )


def run_model(
    model: str,
    mode: Mode,
    expected: ExpectedFile,
    fixtures_dir: Path,
    settings: Settings,
    max_cost_usd: float,
    use_cache: bool = True,
    log: Log = lambda _msg: None,
    llm_factory: Callable[[float], LLMClient] | None = None,
    repeat: int = 1,
    only: set[str] | None = None,
    labels_file: Path | None = None,
) -> EvalResult:
    """`llm_factory(лимит)` подменяет клиента в тестах; по умолчанию — OpenAI-совместимый.

    `only` — имена фикстур для прогона (остальные пропускаются), `labels_file` — файл разметки:
    пути документов считаются от него (по умолчанию — `fixtures_dir`).
    """
    assert mode != "hybrid", "hybrid считается из pipeline и baseline (см. hybrid())"
    rules = thresholds().eval
    th = thresholds()
    spent = 0.0
    runs: list[FixtureRun] = []

    base_file = labels_file or fixtures_dir / "expected.yaml"
    for name, spec in expected.fixtures.items():
        if only is not None and name not in only:
            continue
        remaining = max_cost_usd - spent
        if remaining <= 0:
            runs.append(
                FixtureRun(
                    score=score_fixture(name, spec, [], rules),
                    complete=False,
                    note="пропущен: исчерпан лимит стоимости прогона",
                )
            )
            continue
        options = pipeline.Options(
            model=model, max_cost_usd=remaining, use_cache=use_cache, cache_salt=salt(repeat)
        )
        client = llm_factory(remaining) if llm_factory else pipeline.build_client(settings, options)
        source = detect_input(fixture_path(base_file, name, spec))
        doc = ingest(source)
        started = time.monotonic()
        findings: list[EvalFinding] = []
        complete, note, dropped = True, None, 0

        if mode == "pipeline":
            with tempfile.TemporaryDirectory() as tmp:
                target = resolve_output(source.format, Path(tmp) / "report.json")
                outcome = pipeline.run(source, target, options, settings, llm=client)
            report = outcome.report
            assert report is not None
            findings = [
                EvalFinding(
                    category=f.category.value,
                    severity=f.severity.value,
                    quotes=[f.quote, *f.related_quotes],
                    sections=_sections([f.anchor, *f.related_anchors]),
                    explanation=f.explanation,
                    source=f.source,
                    scope=f.scope,
                    verdict=f.verdict,
                )
                for f in report.findings
                if f.verified
            ]
            complete, note = report.run.complete, report.run.incomplete_reason
            dropped = len(report.debug.dropped_findings)
        else:
            try:
                out = baseline.run(doc, client, criteria(), th.quote_min_similarity)
                findings, dropped = out.findings, out.dropped
            except (LLMCallError, BudgetExceededError) as exc:
                complete, note = False, exc.message

        usage = getattr(client, "usage", None)
        cost = float(usage.cost_usd) if usage else 0.0
        spent += cost
        run = FixtureRun(
            score=score_fixture(name, spec, findings, rules),
            blocks=len(candidate_blocks(doc)),
            cost_usd=cost,
            seconds=round(time.monotonic() - started, 1),
            calls=usage.calls if usage else 0,
            cached_calls=usage.cached_calls if usage else 0,
            complete=complete,
            note=note,
            dropped_unverified=dropped,
        )
        runs.append(run)
        log(
            f"  {name}: находок {run.score.findings_total}, найдено дефектов "
            f"{run.score.found}/{len(run.score.defects)}, ${cost:.4f}, {run.seconds:.0f} с"
        )

    return finish(model, mode, runs, repeat)
