"""Вердикт по тестированию: blocker даёт «с оговорками» и список затронутых требований."""

from reqcheck.config import thresholds
from reqcheck.schemas import Anchor, Category, Finding, Requirement, RequirementKind, Severity
from reqcheck.stages.verdict import decide

REQS = [
    Requirement(
        report_id=f"R-{i:03d}",
        kind=RequirementKind.ACCEPTANCE if i == 3 else RequirementKind.FUNCTIONAL,
        summary="s",
        quote="q",
        anchor=Anchor(),
    )
    for i in (1, 2, 3, 10)
]


def finding(fid: str, rids: list[str], category: Category, severity: Severity) -> Finding:
    return Finding(
        finding_id=fid,
        scope="set" if len(rids) != 1 else "requirement",
        requirement_ids=rids,
        category=category,
        severity=severity,
        verdict="violation",
        quote="q",
        anchor=Anchor(),
        explanation="e",
        source="llm",
    )


def test_blocker_contradiction_gives_testing_caveat_with_requirements() -> None:
    rules = thresholds().verdict
    fs = [
        finding("F-002", ["R-010", "R-002"], Category.CONTRADICTION, Severity.BLOCKER),
        finding("F-001", ["R-001"], Category.AMBIGUOUS, Severity.MAJOR),
    ]
    v = decide(REQS, fs, rules, max_top=5)
    assert not v.can_start_development
    assert v.can_start_testing
    assert "с оговорками" in v.testing
    assert "R-002, R-010" in v.testing and "F-002" in v.testing
    assert "R-001" not in v.testing  # major не задерживает тесты
    assert "с оговорками, если blocker" in v.rule


def test_no_blockers_testing_without_caveat() -> None:
    fs = [finding("F-001", ["R-001"], Category.AMBIGUOUS, Severity.MAJOR)]
    v = decide(REQS, fs, thresholds().verdict, max_top=5)
    assert v.can_start_testing and v.testing == "можно начинать тест-дизайн"


def test_unverifiable_still_blocks_testing() -> None:
    fs = [finding("F-001", ["R-001"], Category.UNVERIFIABLE, Severity.BLOCKER)]
    v = decide(REQS, fs, thresholds().verdict, max_top=5)
    assert not v.can_start_testing and v.testing.startswith("нельзя")


def test_requirement_finding_repeating_set_finding_is_suppressed() -> None:
    from reqcheck.stages.verify import suppress_noise

    rules = thresholds().findings_filter
    set_f = finding("F-001", ["R-001", "R-002"], Category.CONTRADICTION, Severity.BLOCKER)
    set_f = set_f.model_copy(update={"quote": "следующее достижение вызывает push"})
    same = finding("F-002", ["R-002"], Category.AMBIGUOUS, Severity.MAJOR).model_copy(
        update={"quote": "следующее достижение вызывает push", "verdict": "unclear"}
    )
    other = finding("F-003", ["R-002"], Category.AMBIGUOUS, Severity.MAJOR).model_copy(
        update={"quote": "в течение минуты"}
    )
    kept, suppressed = suppress_noise([set_f, same, other], rules)
    assert [f.finding_id for f in suppressed] == ["F-002"]
    assert {f.finding_id for f in kept} == {"F-001", "F-003"}


def test_incorrect_on_contradiction_participant_is_suppressed() -> None:
    from reqcheck.stages.verify import suppress_noise

    set_f = finding("F-001", ["R-001", "R-002"], Category.CONTRADICTION, Severity.BLOCKER)
    side = finding("F-002", ["R-002"], Category.INCORRECT, Severity.MAJOR).model_copy(
        update={"quote": "другая ячейка той же строки"}
    )
    _, suppressed = suppress_noise([set_f, side], thresholds().findings_filter)
    assert [f.finding_id for f in suppressed] == ["F-002"]
