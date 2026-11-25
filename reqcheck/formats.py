"""Форматы входа и выхода (FR-1, FR-2, FR-18; ADR-002: формат отчёта = формат входа)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from reqcheck.errors import InputError

InputFormat = Literal["pdf", "md", "txt"]
OutputFormat = Literal["pdf", "md", "json"]

MAX_INPUT_BYTES = 20 * 1024 * 1024

INPUT_SUFFIXES: dict[str, InputFormat] = {
    ".pdf": "pdf",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
}
OUTPUT_SUFFIXES: dict[str, OutputFormat] = {".pdf": "pdf", ".md": "md", ".json": "json"}
OUTPUT_FOR_INPUT: dict[InputFormat, OutputFormat] = {"pdf": "pdf", "md": "md", "txt": "md"}

SUPPORTED_HINT = "поддерживаются PDF, Markdown (.md, .markdown) и TXT"


@dataclass(frozen=True)
class InputFile:
    path: Path
    format: InputFormat
    size: int


@dataclass(frozen=True)
class OutputTarget:
    path: Path
    format: OutputFormat
    json_path: Path


def detect_input(path: Path) -> InputFile:
    if not path.is_file():
        raise InputError(f"Файл не найден: {path}")
    fmt = INPUT_SUFFIXES.get(path.suffix.lower())
    if fmt is None:
        shown = path.suffix or "без расширения"
        raise InputError(f"Неподдерживаемый формат «{shown}»: {SUPPORTED_HINT}")
    size = path.stat().st_size
    if size == 0:
        raise InputError(f"Файл пустой: {path}")
    if size > MAX_INPUT_BYTES:
        raise InputError(f"Файл больше {MAX_INPUT_BYTES // (1024 * 1024)} МБ: {path}")
    if fmt == "pdf":
        with path.open("rb") as fh:
            if not fh.read(1024).lstrip().startswith(b"%PDF-"):
                raise InputError(f"Файл с расширением .pdf не является PDF: {path}")
    return InputFile(path=path, format=fmt, size=size)


def resolve_output(
    input_format: InputFormat, path: Path, json_path: Path | None = None
) -> OutputTarget:
    """Формат отчёта берётся из расширения пути; без расширения — формат входа (FR-18)."""
    suffix = path.suffix.lower()
    if suffix in OUTPUT_SUFFIXES:
        fmt = OUTPUT_SUFFIXES[suffix]
    elif suffix:
        raise InputError(
            f"Неподдерживаемое расширение отчёта «{suffix}»: нужен .pdf, .md или .json"
        )
    else:
        fmt = OUTPUT_FOR_INPUT[input_format]
        path = path.with_suffix("." + fmt)
    if json_path is None:
        json_path = path if fmt == "json" else path.with_name(path.name + ".json")
    return OutputTarget(path=path, format=fmt, json_path=json_path)
