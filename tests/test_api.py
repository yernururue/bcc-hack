"""Библиотека: validate(...) и report.save (docs/04-integration.md §2)."""

from pathlib import Path

import pytest
from test_pipeline import DOC, FakeLLM

import reqcheck
from reqcheck import Options, validate
from reqcheck.errors import AuthError, InputError
from reqcheck.settings import Settings

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


def test_validate_bytes_events_and_save(tmp_path: Path) -> None:
    events: list = []
    report = validate(
        DOC.encode(),
        "spec.md",
        Options(model="fake/model", max_cost_usd=0.5),
        on_event=events.append,
        settings=SETTINGS,
        llm=FakeLLM(),
    )
    assert isinstance(report, reqcheck.Report)
    assert report.run.document.name == "spec.md"
    assert report.summary.blockers == report.summary.by_severity.get("blocker", 0)
    assert events[0].stage == "ingest" and events[-1].progress == 1.0
    assert [e.progress for e in events] == sorted(e.progress for e in events)

    written = report.save(tmp_path / "out" / "review.pdf")
    assert written[0].read_bytes().startswith(b"%PDF-")
    assert written[1].name == "review.pdf.json"
    only_json = report.save(tmp_path / "r.json")
    assert only_json == [tmp_path / "r.json"]
    assert reqcheck.Report.model_validate_json(only_json[0].read_text()).summary == report.summary
    md = report.save(tmp_path / "r")  # без расширения — формат входа
    assert md[0].suffix == ".md"


def test_validate_path_and_text(tmp_path: Path) -> None:
    src = tmp_path / "a.md"
    src.write_text(DOC, encoding="utf-8")
    assert validate(src, settings=SETTINGS, llm=FakeLLM()).requirements
    assert validate(DOC, "b.md", settings=SETTINGS, llm=FakeLLM()).requirements


def test_validate_errors() -> None:
    with pytest.raises(InputError):
        validate(b"x", None, settings=SETTINGS, llm=FakeLLM())
    with pytest.raises(InputError):
        validate(b"x", "a.docm", settings=SETTINGS, llm=FakeLLM())
    with pytest.raises(AuthError):
        validate(DOC, "a.md", settings=SETTINGS)
