"""PDF → блоки по страницам через pymupdf4llm (Markdown на страницу, затем общий разбор)."""

from pathlib import Path

from reqcheck.errors import InputError, NoTextLayerError
from reqcheck.ingest.markdown import MarkdownParser, ParseState, RawBlock
from reqcheck.ingest.normalize import normalize


def read_pdf(path: Path, parser: MarkdownParser) -> tuple[list[RawBlock], int]:
    # Импорт здесь: pymupdf4llm грузит модель разметки, а для MD/TXT она не нужна.
    import pymupdf
    import pymupdf4llm

    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # повреждённый файл: сообщение PyMuPDF понятнее общего
        raise InputError(f"Не удалось открыть PDF: {exc}") from exc

    with doc:
        if doc.needs_pass:
            raise InputError("PDF защищён паролем: снимите защиту и повторите")
        pages = doc.page_count
        if not any(page.get_text().strip() for page in doc):
            raise NoTextLayerError(
                "В PDF нет текстового слоя (скан). Распознавание (OCR) пока не поддерживается: "
                "сохраните документ как PDF с текстом или в Markdown"
            )
        chunks = pymupdf4llm.to_markdown(
            doc,
            page_chunks=True,
            header=False,  # колонтитулы повторяются на каждой странице и только мешают
            footer=False,
            use_ocr=False,
            show_progress=False,
        )

    state = ParseState()
    blocks: list[RawBlock] = []
    for chunk in chunks:
        page_number = int(chunk["metadata"]["page_number"])
        text = normalize(chunk["text"] or "")
        blocks.extend(parser.parse(text, state, page=page_number, with_lines=False))
    return blocks, pages
