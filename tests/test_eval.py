"""Eval: сопоставление находок, метрики, baseline и прогон на фейковом LLM. Сеть не нужна."""

from pathlib import Path

import pytest
from pydantic import BaseModel
from test_pipeline import FakeLLM

from reqcheck.config import EvalRules, models_config, thresholds
from reqcheck.eval import baseline, tables
from reqcheck.eval.__main__ import FIXTURES
from reqcheck.eval.baseline import BaselineResult
from reqcheck.eval.matching import (
    EvalFinding,
    ExpectedDefect,
    FixtureSpec,
    compute_metrics,
    load_expected,
    quote_overlap,
    score_fixture,
)
from reqcheck.eval.runner import EvalResult, check_tier, result_path, run_model
from reqcheck.formats import detect_input
from reqcheck.ingest import ingest
from reqcheck.llm.base import LLMResult, Usage
from reqcheck.schemas import LLMUsage
from reqcheck.settings import Settings
from reqcheck.stages.extract import candidate_blocks
from reqcheck.stages.quotes import locate

RULES = EvalRules(quote_overlap=80, min_quote_chars=6)


def finding(category: str, *quotes: str, severity: str = "major") -> EvalFinding:
    return EvalFinding(category=category, severity=severity, quotes=list(quotes))


def defect(id_: str, category: str, *quotes: str, accept=(), min_quotes: int = 1):
    return ExpectedDefect(
        id=id_, category=category, accept=list(accept), quotes=list(quotes), min_quotes=min_quotes
    )


# --- сопоставление --------------------------------------------------------------------------


def test_overlap_substring_and_fuzzy() -> None:
    assert (
        quote_overlap("быстро формировать", "Система должна быстро формировать выписку", 6) == 100
    )
    assert quote_overlap("быстро формировать выписку", "быстро формировать выпиской", 6) >= 80
    assert quote_overlap("быстро формировать выписку", "совсем другой текст про заказы", 6) < 80


def test_overlap_ignores_tiny_quotes() -> None:
    assert quote_overlap("должна", "Система должна быстро работать", 6) == 100
    assert quote_overlap("быть", "Система должна быть быстрой", 6) == 0


def test_found_wrong_category_and_missed() -> None:
    spec = FixtureSpec(
        defects=[
            defect("a", "ambiguous", "быстро формировать выписку", accept=["unverifiable"]),
            defect("b", "composite", "регистрировать возврат, пересчитывать баллы"),
            defect("c", "infeasible", "без перерывов и потери заказов"),
        ]
    )
    findings = [
        finding("unverifiable", "быстро формировать выписку"),  # синоним: засчитано
        finding("incomplete", "регистрировать возврат, пересчитывать баллы"),  # место то, класс нет
    ]
    score = score_fixture("f.pdf", spec, findings, RULES)
    assert [d.status for d in score.defects] == ["found", "wrong_category", "missed"]
    assert score.defects[1].matched_category == "incomplete"
    assert (score.found, score.located, score.recall) == (1, 2, pytest.approx(1 / 3))
    assert score.findings_matched == 1 and score.findings_total == 2
    assert len(score.unmatched) == 1


def test_one_finding_covers_one_defect_only() -> None:
    spec = FixtureSpec(
        defects=[
            defect("a", "ambiguous", "быстро формировать выписку"),
            defect("b", "ambiguous", "быстро формировать выписку по заказам"),
        ]
    )
    score = score_fixture(
        "f.pdf", spec, [finding("ambiguous", "быстро формировать выписку")], RULES
    )
    assert score.found == 1


def test_contradiction_needs_both_places() -> None:
    spec = FixtureSpec(
        defects=[
            defect(
                "c",
                "contradiction",
                "не более 5 адресов доставки",
                "хранится до 10 адресов доставки",
                min_quotes=2,
            )
        ]
    )
    one = finding("contradiction", "не более 5 адресов доставки")
    both = finding("contradiction", "не более 5 адресов доставки", "до 10 адресов доставки")
    assert score_fixture("f", spec, [one], RULES).found == 0
    assert score_fixture("f", spec, [both], RULES).found == 1


def test_metrics_per_kind_precision_and_clean() -> None:
    spec = FixtureSpec(
        defects=[
            defect("a", "ambiguous", "быстро формировать выписку"),
            defect("b", "composite", "регистрировать возврат и блокировать аккаунты"),
        ]
    )
    bad = score_fixture(
        "defects.pdf",
        spec,
        [
            finding("ambiguous", "быстро формировать выписку"),
            finding("minor_noise", "совсем другое место документа"),
        ],
        RULES,
    )
    clean = score_fixture(
        "clean.pdf",
        FixtureSpec(),
        [finding("ambiguous", "что-то", severity="minor"), finding("incomplete", "иное")],
        RULES,
    )
    m = compute_metrics([bad, clean])
    assert m.recall == 0.5 and m.recall_by_fixture == {"defects.pdf": 0.5}
    assert m.recall_by_kind == {"ambiguous": (1, 1), "composite": (0, 1)}
    assert m.precision == 0.5
    assert (m.false_positives_clean, m.false_positives_clean_major) == (2, 1)


# --- фикстуры: PDF → ингест → цитаты ---------------------------------------------------------


@pytest.mark.parametrize("name", ["defects.pdf", "contradictions.pdf", "clean.pdf"])
def test_fixture_quotes_are_in_ingested_pdf(name: str) -> None:
    expected = load_expected(FIXTURES / "expected.yaml")
    doc = ingest(detect_input(FIXTURES / name))
    assert doc.format == "pdf" and doc.pages
    blocks = candidate_blocks(doc)
    for d in expected.fixtures[name].defects:
        for quote in d.quotes:
            assert locate(quote, blocks, thresholds().quote_min_similarity), (d.id, quote)


def test_expected_covers_each_kind_once_in_defects() -> None:
    expected = load_expected(FIXTURES / "expected.yaml")
    cats = [d.category.value for d in expected.fixtures["defects.pdf"].defects]
    assert len(cats) == len(set(cats)) >= 10
    assert expected.fixtures["clean.pdf"].defects == []


# --- baseline ---------------------------------------------------------------------------------


class ScriptedLLM:
    model = "fake/baseline"

    def __init__(self, answer: BaseModel) -> None:
        self.answer = answer
        self.usage = LLMUsage()

    def complete(self, *, system: str, user: str, schema, stage: str):
        assert "<document>" in user and stage == "baseline"
        self.usage.calls += 1
        self.usage.cost_usd += 0.002
        return LLMResult(schema.model_validate(self.answer.model_dump()), Usage())


def _bl(category: str, *quotes: str):
    return {
        "category": category,
        "severity": "major",
        "quotes": list(quotes),
        "explanation": "x",
        "suggestion": None,
    }


def test_baseline_verifies_quotes_by_code() -> None:
    doc = ingest(detect_input(FIXTURES / "contradictions.pdf"))
    answer = BaselineResult.model_validate(
        {
            "findings": [
                _bl("contradiction", "не более 5 адресов доставки", "хранится до 10 адресов"),
                _bl("contradiction", "не более 5 адресов доставки", "выдуманная цитата здесь"),
                _bl("ambiguous", "этого текста в документе нет совсем"),
            ]
        }
    )
    out = baseline.run(
        doc, ScriptedLLM(answer), __import__("reqcheck.config").config.criteria(), 90
    )
    assert out.raw == 3 and out.dropped == 2
    assert [f.category for f in out.findings] == ["contradiction"]


# --- прогон модели на фейковом LLM ------------------------------------------------------------


def _expected():
    return load_expected(FIXTURES / "expected.yaml")


def test_run_model_baseline_and_result_json(tmp_path: Path) -> None:
    answer = BaselineResult.model_validate(
        {"findings": [_bl("ambiguous", "быстро формировать выписку по заказам")]}
    )
    result = run_model(
        "fake/baseline",
        "baseline",
        _expected(),
        FIXTURES,
        Settings(_env_file=None),  # type: ignore[call-arg]
        max_cost_usd=1.0,
        llm_factory=lambda _limit: ScriptedLLM(answer),
    )
    by_name = {r.score.fixture: r for r in result.fixtures}
    assert by_name["defects.pdf"].score.found == 1
    # на чистом документе той же цитаты нет: verify отбрасывает находку, ложных нет
    assert by_name["clean.pdf"].score.findings_total == 0
    assert by_name["clean.pdf"].dropped_unverified == 1
    assert result.cost_usd == pytest.approx(0.006)

    path = result_path(tmp_path, result)
    assert path.name.endswith("-fake-baseline-baseline.json")
    path.write_text(result.model_dump_json(), encoding="utf-8")
    again = EvalResult.model_validate_json(path.read_text(encoding="utf-8"))
    assert again == result
    assert "fake/baseline (baseline)" in tables.summary_table([result])
    assert "ambiguous" in tables.kinds_table([result])


def test_run_model_pipeline_with_fake_llm() -> None:
    result = run_model(
        "fake/model",
        "pipeline",
        _expected(),
        FIXTURES,
        Settings(_env_file=None),  # type: ignore[call-arg]
        max_cost_usd=1.0,
        llm_factory=lambda _limit: FakeLLM(),
    )
    defects = next(r for r in result.fixtures if r.score.fixture == "defects.pdf")
    clean = next(r for r in result.fixtures if r.score.fixture == "clean.pdf")
    # «быстро» ловит правило кодом, выдуманные цитаты фейка отбрасывает verify
    assert {d.id: d.status for d in defects.score.defects}["D-ambiguous"] == "found"
    assert defects.dropped_unverified >= 1
    assert clean.score.findings_total == 0  # чистый документ чист для правил кодом
    assert result.metrics.false_positives_clean == 0


def test_budget_stops_remaining_fixtures() -> None:
    result = run_model(
        "fake/model",
        "pipeline",
        _expected(),
        FIXTURES,
        Settings(_env_file=None),  # type: ignore[call-arg]
        max_cost_usd=0.0,
        llm_factory=lambda _limit: FakeLLM(),
    )
    assert all(not r.complete for r in result.fixtures)
    assert result.cost_usd == 0


# --- защита бюджета ---------------------------------------------------------------------------


def test_strong_models_need_explicit_consent() -> None:
    check_tier("deepseek/deepseek-v4.1-flash", allow_strong=False)
    check_tier("openai/gpt-6-luna", allow_strong=False)
    with pytest.raises(ValueError, match="согласие владельца"):
        check_tier("anthropic/claude-sonnet-5.5", allow_strong=False)
    with pytest.raises(ValueError):
        check_tier("unknown/model", allow_strong=False)
    check_tier("anthropic/claude-sonnet-5.5", allow_strong=True)
    assert models_config().get("anthropic/claude-sonnet-5.5").tier == "strong"


def test_ranking_prefers_recall_then_false_positives_then_price() -> None:
    def res(model: str, found: int, fp: int, cost: float) -> EvalResult:
        spec = FixtureSpec(
            defects=[defect(str(i), "ambiguous", f"цитата номер {i}") for i in range(4)]
        )
        findings = [finding("ambiguous", f"цитата номер {i}") for i in range(found)]
        bad = score_fixture("defects.pdf", spec, findings, RULES)
        clean = score_fixture("clean.pdf", FixtureSpec(), [finding("x", "y")] * fp, RULES)
        from reqcheck.eval.runner import FixtureRun

        runs = [FixtureRun(score=bad, cost_usd=cost), FixtureRun(score=clean)]
        return EvalResult(
            date="2026-11-26",
            model=model,
            mode="pipeline",
            agent_version="t",
            fixtures=runs,
            metrics=compute_metrics([bad, clean]),
        )

    a, b, c = res("a", 3, 5, 0.01), res("b", 4, 9, 0.05), res("c", 4, 9, 0.02)
    assert tables.winner([a, b, c]) is c  # recall 4/4 равен у b и c, ложные равны, c дешевле
    assert [r.model for r in sorted([a, b, c], key=tables.rank_key)] == ["c", "b", "a"]
