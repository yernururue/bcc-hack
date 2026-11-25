from pathlib import Path

import pytest
from pydantic import SecretStr

from reqcheck.cli import main
from reqcheck.errors import ExitCode
from reqcheck.settings import Settings


def settings(key: str | None = None) -> Settings:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    return s.model_copy(update={"llm_api_key": SecretStr(key) if key else None})


@pytest.fixture
def doc(tmp_path: Path) -> Path:
    p = tmp_path / "req.md"
    p.write_text("# Требования\n\n* **Ф-1.** Система сохраняет порог.\n", encoding="utf-8")
    return p


def test_missing_input_is_bad_input(tmp_path: Path) -> None:
    code = main([str(tmp_path / "nope.md"), str(tmp_path / "out.md")], settings=settings("k"))
    assert code == ExitCode.BAD_INPUT


def test_unsupported_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "spec.docx"
    f.write_bytes(b"x")
    assert main([str(f), str(tmp_path / "o.md")], settings=settings("k")) == ExitCode.BAD_INPUT
    assert "поддерживаются PDF" in capsys.readouterr().err


Capsys = pytest.CaptureFixture[str]


def test_no_key_is_auth_error(doc: Path, tmp_path: Path, capsys: Capsys) -> None:
    assert main([str(doc), str(tmp_path / "o")], settings=settings()) == ExitCode.AUTH
    assert "openrouter.ai/keys" in capsys.readouterr().err


def test_dry_run_needs_no_key_and_resolves_output(
    doc: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([str(doc), str(tmp_path / "o"), "--dry-run"], settings=settings())
    err = capsys.readouterr().err
    assert f"{tmp_path / 'o.md'} (md)" in err
    assert "deepseek/deepseek-v4.1-flash" in err
    assert code == ExitCode.OK  # dry-run: только разбор, без вызовов LLM
    assert "блоков 2, с номером пункта 1" in err


def test_pdf_report_not_ready_fails_before_llm(doc: Path, tmp_path: Path, capsys: Capsys) -> None:
    code = main([str(doc), str(tmp_path / "o.pdf")], settings=settings("k"))
    assert code == ExitCode.ERROR
    assert ".md" in capsys.readouterr().err


def test_estimate_printed_in_dry_run(doc: Path, tmp_path: Path, capsys: Capsys) -> None:
    main([str(doc), str(tmp_path / "o.md"), "--dry-run"], settings=settings())
    assert "Оценка:" in capsys.readouterr().err


def test_model_and_cost_flags(doc: Path, tmp_path: Path, capsys: Capsys) -> None:
    args = [str(doc), str(tmp_path / "o.md"), "--dry-run"]
    main([*args, "--model", "openai/gpt-6-luna", "--max-cost", "0.1"], settings=settings())
    err = capsys.readouterr().err
    assert "openai/gpt-6-luna" in err
    assert "$0.10" in err


def test_nonpositive_cost_rejected(doc: Path, tmp_path: Path) -> None:
    code = main([str(doc), str(tmp_path / "o.md"), "--max-cost", "0"], settings=settings("k"))
    assert code == ExitCode.ERROR
