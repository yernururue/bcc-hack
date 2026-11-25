"""Этап verdict: счётчики и решение «можно ли начинать» считает код по thresholds.yaml (ADR-003)."""

from reqcheck.config import VerdictRules
from reqcheck.schemas import (
    Category,
    Critical,
    Finding,
    Requirement,
    RequirementKind,
    Severity,
    Summary,
    Verdict,
)
from reqcheck.stages.verify import SEVERITY_RANK


def _violations(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.verdict == "violation"]


def summarize(reqs: list[Requirement], findings: list[Finding], split: int) -> Summary:
    violations = _violations(findings)
    by_severity = {s: sum(1 for f in violations if f.severity == s) for s in Severity}

    def ids(*cats: Category) -> list[str]:
        return [f.finding_id for f in violations if f.category in cats]

    return Summary(
        requirements_total=len(reqs),
        split_composites=split,
        violations_total=len(violations),
        unclear_total=sum(1 for f in findings if f.verdict == "unclear"),
        by_severity=by_severity,
        critical=Critical(
            unverifiable=ids(Category.UNVERIFIABLE),
            contradictory=ids(Category.CONTRADICTION),
            incomplete=ids(Category.INCOMPLETE, Category.MISSING_TOPIC),
        ),
    )


def decide(
    reqs: list[Requirement], findings: list[Finding], rules: VerdictRules, max_top: int
) -> Verdict:
    violations = _violations(findings)
    blockers = sum(1 for f in violations if f.severity == Severity.BLOCKER)
    majors = sum(1 for f in violations if f.severity == Severity.MAJOR)
    unverifiable = sum(1 for f in violations if f.category == Category.UNVERIFIABLE)
    no_acceptance = not any(r.kind == RequirementKind.ACCEPTANCE for r in reqs)

    can_dev = blockers < rules.block_development_if_blockers_at_least
    if not can_dev:
        dev = f"нельзя начинать — блокирующих дефектов: {blockers}"
    elif majors >= rules.caution_if_major_at_least:
        dev = f"можно начинать с оговорками — существенных дефектов: {majors}"
    else:
        dev = "можно начинать"

    test_reasons: list[str] = []
    if unverifiable >= rules.block_testing_if_unverifiable_at_least:
        test_reasons.append(f"непроверяемых требований: {unverifiable}")
    if rules.block_testing_if_no_acceptance and no_acceptance:
        test_reasons.append("нет критериев приёмки")
    can_test = not test_reasons
    test = (
        "можно начинать тест-дизайн"
        if can_test
        else ("нельзя начинать тест-дизайн — " + ", ".join(test_reasons))
    )

    rule = (
        f"Разработка: нельзя, если blocker ≥ {rules.block_development_if_blockers_at_least}; "
        f"с оговорками, если blocker = 0 и major ≥ {rules.caution_if_major_at_least}. "
        f"Тестирование: нельзя, если непроверяемых ≥ {rules.block_testing_if_unverifiable_at_least}"
        + (" или нет критериев приёмки" if rules.block_testing_if_no_acceptance else "")
        + ". Источник: config/thresholds.yaml"
    )
    top = sorted(
        violations,
        key=lambda f: (SEVERITY_RANK[f.severity], -len(f.requirement_ids), f.finding_id),
    )[:max_top]
    return Verdict(
        can_start_development=can_dev,
        can_start_testing=can_test,
        reason=f"Разработка: {dev}. Тестирование: {test}.",
        development=dev,
        testing=test,
        rule=rule,
        top_fixes=[f.finding_id for f in top],
    )
