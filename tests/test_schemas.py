from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from reqcheck.schemas import (
    Anchor,
    Category,
    DocumentInfo,
    Finding,
    Question,
    Report,
    Requirement,
    RequirementKind,
    RunPassport,
    Severity,
    Verdict,
)


def test_anchor_label_markdown() -> None:
    a = Anchor(section_path=["Часть A", "Функциональные требования"], item="Ф-2", line=38)
    assert a.label() == "Часть A › Функциональные требования › Ф-2 (строка 38)"


def test_anchor_label_pdf_has_page_and_no_line() -> None:
    a = Anchor(page=3, section_path=["4.2 Требования"], item="4.2.1", line=12)
    assert a.label() == "стр. 3 › 4.2 Требования › 4.2.1"


def test_anchor_label_empty() -> None:
    assert Anchor().label() == "документ"


def test_anchor_kazakh_text_roundtrip() -> None:
    a = Anchor(section_path=["Талаптар", "Қауіпсіздік"], item="Ә-1")
    assert Anchor.model_validate_json(a.model_dump_json()) == a


def _finding(**kw) -> Finding:
    base = dict(
        finding_id="F-001",
        scope="requirement",
        requirement_ids=["R-001"],
        category=Category.UNVERIFIABLE,
        severity=Severity.BLOCKER,
        verdict="violation",
        quote="в течение минуты",
        anchor=Anchor(section_path=["НФТ"]),
        explanation="нет критерия",
        source="llm",
    )
    base.update(kw)
    return Finding(**base)


def _report() -> Report:
    return Report(
        run=RunPassport(
            document=DocumentInfo(name="req.md", sha256="0" * 64, format="md"),
            started_at=datetime(2026, 11, 26, 9, 0, tzinfo=UTC),
            model="deepseek/deepseek-v4.1-flash",
            provider_base_url="https://openrouter.ai/api/v1",
            agent_version="v0.1.0",
        ),
        requirements=[
            Requirement(
                report_id="R-001",
                doc_id="Ф-1",
                kind=RequirementKind.FUNCTIONAL,
                summary="Порог от 1 000 до 1 000 000 ₸",
                quote="задаёт целый порог от 1 000 до 1 000 000 ₸ включительно",
                anchor=Anchor(item="Ф-1"),
                assessment={"verifiable": "ok"},
            )
        ],
        findings=[_finding()],
        verdict=Verdict(
            can_start_development=False,
            can_start_testing=False,
            reason="1 блокирующий дефект",
            rule="blocker >= 1",
            top_fixes=["F-001"],
        ),
    )


def test_report_json_roundtrip() -> None:
    r = _report()
    again = Report.model_validate_json(r.model_dump_json())
    assert again == r
    assert again.schema_version == "1.0"


def test_extra_fields_forbidden() -> None:
    data = _report().model_dump(mode="json")
    data["unexpected"] = 1
    with pytest.raises(ValidationError):
        Report.model_validate(data)


@pytest.mark.parametrize("bad_id", ["R1", "F-001", "R-01"])
def test_requirement_id_pattern(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        Requirement(
            report_id=bad_id,
            kind=RequirementKind.OTHER,
            summary="s",
            quote="q",
            anchor=Anchor(),
        )


def test_question_options_bounds() -> None:
    with pytest.raises(ValidationError):
        Question(id="Q-1", question="?", options=["только один"], default_assumption="a")
    with pytest.raises(ValidationError):
        Question(id="Q-1", question="?", options=list("abcde"), default_assumption="a")


def test_top_fixes_limited_to_five() -> None:
    with pytest.raises(ValidationError):
        Verdict(
            can_start_development=True,
            can_start_testing=True,
            reason="",
            rule="",
            top_fixes=[f"F-00{i}" for i in range(6)],
        )
