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
    score_fixture,
)
from reqcheck.formats import detect_input, resolve_output
from reqcheck.ingest import ingest
from reqcheck.llm.base import LLMClient
from reqcheck.llm.openai_compat import LLMCallError
from reqcheck.schemas.common import Strict
from reqcheck.settings import Settings
from reqcheck.version import agent_version

Mode = Literal["pipeline", "baseline"]
Log = Callable[[str], None]


class FixtureRun(Strict):
    score: FixtureScore
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
    fixtures: list[FixtureRun] = Field(default_factory=list)
    metrics: Metrics

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


def result_path(out_dir: Path, result: EvalResult) -> Path:
    suffix = "-baseline" if result.mode == "baseline" else ""
    return out_dir / f"{result.date}-{slug(result.model)}{suffix}.json"


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
) -> EvalResult:
    """`llm_factory(лимит)` подменяет клиента в тестах; по умолчанию — OpenAI-совместимый."""
    rules = thresholds().eval
    th = thresholds()
    spent = 0.0
    runs: list[FixtureRun] = []

    for name, spec in expected.fixtures.items():
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
        options = pipeline.Options(model=model, max_cost_usd=remaining, use_cache=use_cache)
        client = llm_factory(remaining) if llm_factory else pipeline.build_client(settings, options)
        source = detect_input(fixtures_dir / name)
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
            doc = ingest(source)
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

    return EvalResult(
        date=date.today().isoformat(),
        model=model,
        mode=mode,
        agent_version=agent_version(),
        fixtures=runs,
        metrics=compute_metrics([r.score for r in runs]),
    )
