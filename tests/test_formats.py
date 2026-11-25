from pathlib import Path

import pytest

from reqcheck import formats
from reqcheck.errors import ExitCode, InputError
from reqcheck.formats import detect_input, resolve_output


def write(tmp_path: Path, name: str, data: bytes = b"# req\n") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


@pytest.mark.parametrize(
    ("name", "fmt"),
    [("a.md", "md"), ("a.MARKDOWN", "md"), ("a.txt", "txt")],
)
def test_detect_text_formats(tmp_path: Path, name: str, fmt: str) -> None:
    assert detect_input(write(tmp_path, name)).format == fmt


def test_detect_pdf_by_signature(tmp_path: Path) -> None:
    assert detect_input(write(tmp_path, "a.pdf", b"%PDF-1.7\n...")).format == "pdf"


def test_fake_pdf_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="не является PDF"):
        detect_input(write(tmp_path, "a.pdf", b"not a pdf"))


@pytest.mark.parametrize("name", ["a.docx", "a", "a.html"])
def test_unsupported_format(tmp_path: Path, name: str) -> None:
    with pytest.raises(InputError) as e:
        detect_input(write(tmp_path, name))
    assert e.value.exit_code == ExitCode.BAD_INPUT
    assert "PDF" in e.value.message


def test_missing_and_empty(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="не найден"):
        detect_input(tmp_path / "nope.md")
    with pytest.raises(InputError, match="пустой"):
        detect_input(write(tmp_path, "e.md", b""))


def test_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(formats, "MAX_INPUT_BYTES", 4)
    with pytest.raises(InputError, match="больше"):
        detect_input(write(tmp_path, "big.md", b"12345"))


@pytest.mark.parametrize(
    ("input_fmt", "out", "expected_path", "expected_fmt"),
    [
        ("pdf", "out/r", "out/r.pdf", "pdf"),
        ("md", "out/r", "out/r.md", "md"),
        ("txt", "out/r", "out/r.md", "md"),
        ("md", "out/r.pdf", "out/r.pdf", "pdf"),
        ("pdf", "out/r.json", "out/r.json", "json"),
    ],
)
def test_resolve_output(input_fmt, out, expected_path, expected_fmt) -> None:
    t = resolve_output(input_fmt, Path(out))
    assert t.path == Path(expected_path)
    assert t.format == expected_fmt


def test_json_sidecar_next_to_report() -> None:
    assert resolve_output("pdf", Path("out/r.pdf")).json_path == Path("out/r.pdf.json")
    assert resolve_output("pdf", Path("out/r.json")).json_path == Path("out/r.json")
    assert resolve_output("md", Path("r.md"), Path("x/y.json")).json_path == Path("x/y.json")


def test_unknown_output_suffix() -> None:
    with pytest.raises(InputError, match="расширение отчёта"):
        resolve_output("md", Path("r.docx"))
