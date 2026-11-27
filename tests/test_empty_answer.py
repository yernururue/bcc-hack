"""Вырожденный ответ модели (находки без цитат и пояснений): повтор и пометка «неполный»."""

import json
from pathlib import Path

import pytest
from test_pipeline import DOC, FakeLLM

from reqcheck import pipeline
from reqcheck.formats import detect_input, resolve_output
from reqcheck.llm.base import LLMResult, Usage
from reqcheck.llm.openai_compat import LLMCallError
from reqcheck.schemas import Report
from reqcheck.settings import Settings
from reqcheck.stages.check_set import SetFinding
from reqcheck.stages.empty import is_empty, with_retry

EMPTY = SetFinding(
    category="contradiction",
    severity="blocker",
    requirement_ids=[],
    quotes=[],
    explanation="",
    suggestion=None,
)
REAL = EMPTY.model_copy(update={"quotes": ["порог"], "explanation": "противоречие"})


def test_is_empty() -> None:
    assert is_empty([EMPTY]) and is_empty([EMPTY, EMPTY])
    assert not is_empty([])  # «проблем нет» — нормальный ответ
    assert not is_empty([EMPTY, REAL])
    assert not is_empty([EMPTY.model_copy(update={"explanation": "есть текст"})])


def test_retry_once_with_correction() -> None:
    asked: list[str] = []
    answers = [[EMPTY], [REAL]]

    def ask(user: str) -> list[SetFinding]:
        asked.append(user)
        return answers[len(asked) - 1]

    assert with_retry(ask, "задание", "check_set") == [REAL]
    assert len(asked) == 2 and asked[0] == "задание"
    assert asked[1].startswith("задание") and "без цитат" in asked[1]  # другой ключ кэша


def test_no_retry_for_normal_or_empty_list() -> None:
    calls: list[str] = []
    assert with_retry(lambda u: calls.append(u) or [], "x", "check_set") == []
    assert len(calls) == 1


def test_twice_empty_is_stage_error() -> None:
    with pytest.raises(LLMCallError, match="дважды"):
        with_retry(lambda u: [EMPTY], "x", "check_set")


class EmptySetLLM(FakeLLM):
    """check_set всегда отвечает одной пустой находкой, остальные этапы как обычно."""

    def complete(self, *, system, user, schema, stage):  # type: ignore[override]
        if stage == "check_set":
            self.stages.append(stage)
            self.usage.calls += 1
            return LLMResult(schema.model_validate({"findings": [EMPTY.model_dump()]}), Usage())
        return super().complete(system=system, user=user, schema=schema, stage=stage)


def test_pipeline_marks_report_incomplete_after_two_empty_answers(tmp_path: Path) -> None:
    src = tmp_path / "req.md"
    src.write_text(DOC, encoding="utf-8")
    source = detect_input(src)
    target = resolve_output(source.format, tmp_path / "out" / "report.md")
    llm = EmptySetLLM()
    options = pipeline.Options(model="fake/model", max_cost_usd=1.0)
    pipeline.run(source, target, options, Settings(_env_file=None), llm=llm)  # type: ignore[call-arg]
    assert llm.stages.count("check_set") == 2
    report = Report.model_validate(json.loads(Path(f"{target.path}.json").read_text("utf-8")))
    assert report.run.complete is False
    assert "check_set" in (report.run.incomplete_reason or "")
