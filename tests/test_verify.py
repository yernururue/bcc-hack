"""Проверка цитат и правила кодом: основа «находка без цитаты не попадает в отчёт»."""

import pytest

from reqcheck.config import lexicon
from reqcheck.schemas import Anchor
from reqcheck.schemas.document import Block
from reqcheck.stages.quotes import locate, similarity
from reqcheck.stages.rules import quote_window

TEXT = "Сервис передаёт push на отправку в течение минуты после получения события."


@pytest.mark.parametrize(
    "quote",
    [
        "передаёт push на отправку",
        "передает push на отправку",  # ё/е
        "«в течение минуты»",  # кавычки
        "в течение минуты",  # неразрывный пробел
        "**в течение минуты**",  # разметка
        "передаёт push на отправку в течение минуты после получения события",
    ],
)
def test_quote_found(quote: str) -> None:
    assert similarity(quote, TEXT) >= 90


@pytest.mark.parametrize(
    "quote",
    [
        "мгновенно отправляет уведомление",  # выдумано
        TEXT + " И ещё много текста, которого в блоке нет совсем.",  # длиннее текста
        "в",  # слишком коротко
    ],
)
def test_quote_rejected(quote: str) -> None:
    assert similarity(quote, TEXT) < 90


def test_hyphenated_pdf_text() -> None:
    from reqcheck.ingest.normalize import normalize

    assert similarity("получения события", normalize("после полу-\nчения события")) >= 90


def test_locate_prefers_given_blocks() -> None:
    a = Block(block_id="B-0001", kind="paragraph", text=TEXT, anchor=Anchor(line=1))
    b = Block(block_id="B-0002", kind="paragraph", text=TEXT, anchor=Anchor(line=9))
    assert locate("в течение минуты", [a, b], 90, prefer=[b]) is b
    assert locate("нет такого текста здесь", [a, b], 90) is None


def test_quote_window_is_verbatim() -> None:
    text = "Пользователь может быстро и удобно сохранить порог в настройках карты."
    start = text.index("быстро")
    q = quote_window(text, start, start + len("быстро"), 2)
    assert q == "Пользователь может быстро и удобно"
    assert q in text


def test_lexicon_matches_word_forms_not_substrings() -> None:
    vague = lexicon().vague.compiled()
    assert vague.search("Ответ приходит Быстрее")
    assert vague.search("и т.д.")
    assert not vague.search("предоставляет простоту")  # «простоту» не в списке форм
    assert not vague.search("максимум один push")


def test_suppress_noise_keeps_set_and_top_findings() -> None:
    from reqcheck.config import FindingsFilter
    from reqcheck.schemas import Finding
    from reqcheck.stages.verify import suppress_noise

    def f(sev: str, verdict: str = "violation", scope: str = "requirement") -> Finding:
        return Finding(
            finding_id="F-000",
            scope=scope,
            requirement_ids=["R-001"],
            category="ambiguous",
            severity=sev,
            verdict=verdict,
            quote="q",
            anchor=Anchor(),
            explanation="e",
            source="llm",
        )

    items = [
        f("minor"),
        f("minor", "unclear"),
        f("blocker"),
        f("major"),
        f("minor"),
        f("minor", scope="set"),
    ]
    kept, suppressed = suppress_noise(
        items, FindingsFilter(max_per_requirement=3, drop_unclear_up_to="minor")
    )
    assert [x.severity for x in kept if x.scope == "requirement"] == ["blocker", "major", "minor"]
    assert any(x.scope == "set" for x in kept)
    assert len(suppressed) == 2


def _finding(category: str, sev: str, verdict: str = "violation", scope: str = "set"):
    from reqcheck.schemas import Finding

    return Finding(
        finding_id="F-000",
        scope=scope,
        requirement_ids=["R-001"],
        category=category,
        severity=sev,
        verdict=verdict,
        quote="q",
        anchor=Anchor(),
        explanation="e",
        source="llm",
    )


def test_unclear_dropped_up_to_configured_severity() -> None:
    from reqcheck.config import FindingsFilter
    from reqcheck.stages.verify import suppress_noise

    rules = FindingsFilter(max_per_requirement=3, drop_unclear_up_to="major")
    items = [
        _finding("ambiguous", "major", "unclear", "requirement"),
        _finding("ambiguous", "blocker", "unclear", "requirement"),
        _finding("ambiguous", "major", "violation", "requirement"),
    ]
    kept, suppressed = suppress_noise(items, rules)
    assert [(f.severity, f.verdict) for f in kept] == [
        ("blocker", "unclear"),
        ("major", "violation"),
    ]
    assert len(suppressed) == 1


def test_set_findings_capped_per_category_keeping_most_severe() -> None:
    from reqcheck.config import FindingsFilter
    from reqcheck.stages.verify import suppress_noise

    rules = FindingsFilter(
        max_per_requirement=3, drop_unclear_up_to="minor", max_set_per_category={"missing_topic": 2}
    )
    items = [
        _finding("missing_topic", "minor"),
        _finding("missing_topic", "major"),
        _finding("missing_topic", "blocker"),
        _finding("contradiction", "blocker"),
        _finding("contradiction", "blocker"),
    ]
    kept, suppressed = suppress_noise(items, rules)
    assert sorted(f.severity for f in kept if f.category == "missing_topic") == ["blocker", "major"]
    assert [f.severity for f in suppressed] == ["minor"]
    assert sum(f.category == "contradiction" for f in kept) == 2  # без лимита — не трогаем


def test_model_cannot_raise_severity_above_cap() -> None:
    from reqcheck.config import thresholds
    from reqcheck.schemas import Category, RequirementKind, Severity
    from reqcheck.stages.verify import final_severity

    th = thresholds()
    fn = RequirementKind.FUNCTIONAL
    assert final_severity(Category.TRACEABILITY, Severity.BLOCKER, [fn], th) == Severity.MINOR
    assert final_severity(Category.MISSING_TOPIC, Severity.BLOCKER, [fn], th) == Severity.MAJOR
    # без потолка действует прежнее правило: не ниже умолчания, модель может повысить
    assert final_severity(Category.INCOMPLETE, Severity.BLOCKER, [fn], th) == Severity.BLOCKER
    assert final_severity(Category.CONTRADICTION, Severity.MINOR, [fn], th) == Severity.BLOCKER


def test_gap_findings_may_anchor_on_section_title() -> None:
    """missing_topic/traceability без места в тексте: якорь — проверенный заголовок раздела.
    Другим категориям заголовок как цитата не годится."""
    from pathlib import Path

    from reqcheck.config import criteria, thresholds
    from reqcheck.formats import detect_input
    from reqcheck.ingest import ingest
    from reqcheck.stages import verify
    from reqcheck.stages.check_set import SetFinding

    doc = ingest(detect_input(Path(__file__).parent / "fixtures" / "clinic.md"))

    def sf(category: str, quote: str) -> SetFinding:
        return SetFinding(
            category=category,
            severity="major",
            requirement_ids=[],
            quotes=[quote],
            explanation="x",
            suggestion=None,
        )

    out = verify.set_findings(
        [sf("missing_topic", "2.5. Оплата"), sf("terminology", "2.5. Оплата")],
        [],
        doc,
        thresholds(),
        criteria(),
    )
    assert [f.category.value for f in out.findings] == ["missing_topic"]
    assert out.findings[0].anchor.section_path[-1] == "2.5. Оплата"
    assert [f.category.value for f in out.dropped] == ["terminology"]
