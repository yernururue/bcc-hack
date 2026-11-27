"""Этап ingest: файл → Document (блоки текста с якорями). LLM не используется."""

import hashlib

from reqcheck.config import IngestConfig, ingest_config
from reqcheck.errors import InputError
from reqcheck.formats import InputFile
from reqcheck.ingest.markdown import MarkdownParser, ParseState, RawBlock
from reqcheck.ingest.normalize import normalize
from reqcheck.schemas import Anchor
from reqcheck.schemas.document import Block, Document

__all__ = ["ingest", "decode_text"]


def decode_text(data: bytes) -> str:
    """UTF-8 (с BOM или без), иначе cp1251 — так сохраняют TXT в русской Windows."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1251")
    except UnicodeDecodeError as exc:
        raise InputError("Не удалось прочитать текст: ожидается UTF-8 или Windows-1251") from exc


def _drop_title(raw: list[RawBlock]) -> list[RawBlock]:
    """Единственный H1 в начале — название документа, а не раздел: убираем его из путей."""
    h1 = [b for b in raw if b.kind == "heading" and b.level == 1]
    first_heading = next((b for b in raw if b.kind == "heading"), None)
    if len(h1) != 1 or first_heading is not h1[0]:
        return raw
    title = h1[0].text
    for b in raw:
        if b.section[:1] == (title,):
            b.section = b.section[1:]
    return raw


def ingest(source: InputFile, cfg: IngestConfig | None = None) -> Document:
    cfg = cfg or ingest_config()
    parser = MarkdownParser(cfg)
    data = source.path.read_bytes()
    pages: int | None = None

    if source.format == "pdf":
        from reqcheck.ingest.pdf import read_pdf

        raw, pages = read_pdf(source.path, parser)
    elif source.format == "docx":
        from reqcheck.ingest.docx import docx_to_markdown

        # Номера строк сгенерированного Markdown пользователю ничего не скажут: только разделы
        raw = parser.parse(
            normalize(docx_to_markdown(source.path)), ParseState(), page=None, with_lines=False
        )
    else:
        text = normalize(decode_text(data))
        raw = parser.parse(text, ParseState(), page=None, with_lines=True)

    if cfg.drop_single_h1_title:
        raw = _drop_title(raw)
    if not raw:
        raise InputError("В документе нет текста")

    blocks = [
        Block(
            block_id=f"B-{n:04d}",
            kind=b.kind,
            text=b.text,
            anchor=Anchor(page=b.page, section_path=list(b.section), item=b.item, line=b.line),
        )
        for n, b in enumerate(raw, start=1)
    ]
    return Document(
        source_name=source.path.name,
        sha256=hashlib.sha256(data).hexdigest(),
        format=source.format,
        pages=pages,
        blocks=blocks,
    )
