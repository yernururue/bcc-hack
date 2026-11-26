"""Eval: сопоставление находок, метрики, baseline и прогон на фейковом LLM. Сеть не нужна."""

from pathlib import Path

import pytest
from pydantic import BaseModel
from test_pipeline import FakeLLM

from reqcheck.config import EvalRules, models_config, thresholds
from reqcheck.eval import baseline, tables
from reqcheck.eval.__main__ import FIXTURES, ROOT, load_labels, selected
from reqcheck.eval.baseline import BaselineResult
from reqcheck.eval.matching import (
    ANYWHERE,
    EvalFinding,
    ExpectedDefect,
    FixtureSpec,
    compute_metrics,
    fixture_path,
    load_expected,
    quote_overlap,
    score_fixture,
)
from reqcheck.eval.runner import EvalResult, check_tier, hybrid, result_path, run_model, salt
from reqcheck.formats import detect_input
from reqcheck.ingest import ingest
from reqcheck.llm.base import LLMResult, Usage
from reqcheck.schemas import Category, LLMUsage
from reqcheck.settings import Settings
from reqcheck.stages.extract import candidate_blocks
from reqcheck.stages.quotes import locate, locate_heading, match_key

RULES = EvalRules(quote_overlap=80, min_quote_chars=6)
ORIGINAL = {"defects.pdf", "contradictions.pdf", "clean.pdf"}  # быстрые прогоны на фейках


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
                "Максимальное число адресов доставки в профиле покупателя равно 10",
                min_quotes=2,
            )
        ]
    )
    one = finding("contradiction", "не более 5 адресов доставки")
    both = finding(
        "contradiction",
        "не более 5 адресов доставки",
        "Максимальное число адресов доставки в профиле покупателя равно 10",
    )
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


LABEL_FILES = [FIXTURES / "expected.yaml", *sorted((ROOT / "eval" / "labels").glob("*.yaml"))]
ALL_FIXTURES = [(path, name) for path in LABEL_FILES for name in load_expected(path).fixtures]


@pytest.mark.parametrize(("labels", "name"), ALL_FIXTURES, ids=[n for _, n in ALL_FIXTURES])
def test_fixture_anchors_are_in_ingested_document(labels: Path, name: str) -> None:
    spec = load_expected(labels).fixtures[name]
    doc = ingest(detect_input(fixture_path(labels, name, spec)))
    if doc.format == "pdf":
        assert doc.pages
    blocks = candidate_blocks(doc)
    headings = [match_key(b.text) for b in doc.blocks if b.kind == "heading"]
    for d in spec.defects:
        for quote in d.quotes:
            assert locate(quote, blocks, thresholds().quote_min_similarity), (d.id, quote)
        for section in d.sections:
            assert section == ANYWHERE or any(match_key(section) in h for h in headings), d.id


def test_corpus_covers_every_category_in_both_splits() -> None:
    expected = load_expected(FIXTURES / "expected.yaml")
    by_split: dict[str, set[str]] = {}
    for spec in expected.fixtures.values():
        by_split.setdefault(spec.split, set()).update(d.category.value for d in spec.defects)
    every = {c.value for c in Category}
    assert by_split["dev"] == every and by_split["holdout"] == every
    clean = [n for n, s in expected.fixtures.items() if not s.defects]
    assert {expected.fixtures[n].split for n in clean} == {"dev", "holdout"}


def test_organizer_labels_do_not_leak_into_config_or_prompts() -> None:
    """Разметка документа организаторов — только для eval (ADR-012)."""
    labels = load_expected(ROOT / "eval" / "labels" / "organizers.yaml")
    assert labels.note and "unreviewed" in labels.note
    corpus = " ".join(
        match_key(p.read_text(encoding="utf-8"))
        for p in [*(ROOT / "config").glob("*.yaml"), *(ROOT / "reqcheck" / "prompts").glob("*.md")]
    )
    for spec in labels.fixtures.values():
        assert spec.split == "holdout"
        for d in spec.defects:
            for quote in d.quotes:
                if len(match_key(quote)) >= 15:  # короткие общие термины (HTTP-заголовки) не утечка
                    assert match_key(quote) not in corpus, (d.id, quote)


def test_section_mode_matches_by_anchor_section() -> None:
    gap = ExpectedDefect(
        id="G", category="missing_topic", accept=["incomplete"], sections=["Оплата"]
    )
    anywhere = ExpectedDefect(id="T", category="traceability", sections=[ANYWHERE])
    spec = FixtureSpec(defects=[gap, anywhere])
    findings = [
        EvalFinding(category="missing_topic", severity="major", quotes=["x"], sections=["Учёт"]),
        EvalFinding(
            category="incomplete", severity="major", quotes=["y"], sections=["2.5. Оплата"]
        ),
        EvalFinding(category="traceability", severity="minor", quotes=["z"], sections=[]),
    ]
    score = score_fixture("doc", spec, findings, RULES)
    assert [d.status for d in score.defects] == ["found", "found"]
    with pytest.raises(ValueError, match="ровно один режим"):
        ExpectedDefect(id="X", category="missing_topic", quotes=["a"], sections=["b"])


def test_heading_anchor_only_for_whole_titles() -> None:
    doc = ingest(detect_input(FIXTURES / "clinic.md"))
    block = locate_heading("Оплата", doc.blocks, 90)
    assert (
        block is not None and block.kind == "heading" and "Оплата" in block.anchor.section_path[-1]
    )
    assert locate_heading("Опла", doc.blocks, 90) is None
    assert locate_heading("Онлайн-запись к пациенту в другом городе", doc.blocks, 90) is None


def test_labels_merge_and_split_filter() -> None:
    labels = load_labels(FIXTURES, [ROOT / "eval" / "labels" / "organizers.yaml"])
    assert Path(labels.fixtures["organizers-requirements.md"].path).is_absolute()
    assert "organizers-requirements.md" in selected(labels, "holdout", [])
    assert "organizers-requirements.md" not in selected(labels, "dev", [])
    assert selected(labels, "all", ["clinic.md"]) == {"clinic.md"}


def test_repeat_salt_and_hybrid_routing() -> None:
    assert salt(1) == "" and salt(2) != salt(3)
    small = FixtureSpec(defects=[defect("a", "ambiguous", "быстро формировать выписку")])
    found = [finding("ambiguous", "быстро формировать выписку")]

    def result(mode: str, findings_by_doc: dict) -> EvalResult:
        from reqcheck.eval.runner import FixtureRun, finish

        runs = [
            FixtureRun(score=score_fixture(n, small, f, RULES), blocks=b)
            for n, (b, f) in findings_by_doc.items()
        ]
        return finish("m", mode, runs)

    pipe = result("pipeline", {"small": (10, []), "big": (100, found)})
    base = result("baseline", {"small": (10, found), "big": (100, [])})
    h = hybrid(pipe, base, 40)
    assert h.mode == "hybrid" and h.metrics.recall == 1.0
    assert {f.score.fixture: f.routed for f in h.fixtures} == {
        "small": "baseline",
        "big": "pipeline",
    }
    assert "hybrid" in tables.repeat_table([pipe, base, h])


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
                _bl(
                    "contradiction",
                    "не более 5 адресов доставки",
                    "Максимальное число адресов доставки в профиле покупателя равно 10",
                ),
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
        only=ORIGINAL,
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
        only=ORIGINAL,
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
        only=ORIGINAL,
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
