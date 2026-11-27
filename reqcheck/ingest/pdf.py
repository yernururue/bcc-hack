"""PDF → блоки по страницам через pymupdf4llm (Markdown на страницу, затем общий разбор)."""

from pathlib import Path
from typing import Any

from reqcheck.config import OcrConfig
from reqcheck.errors import InputError, NoTextLayerError
from reqcheck.ingest.markdown import MarkdownParser, ParseState, RawBlock
from reqcheck.ingest.normalize import normalize


def tesseract_ocr(cfg: OcrConfig) -> Any:
    """Функция OCR pymupdf4llm на Tesseract; понятная ошибка, если Tesseract или языков нет.

    Модуль pymupdf4llm.ocr.tesseract_api при импорте сам ищет tessdata, поэтому проверяем до
    импорта. Выбор «лучшего» движка pymupdf4llm не используем: он молча берёт RapidOCR, если тот
    установлен, а нам нужен предсказуемый результат.
    """
    import pymupdf

    try:
        tessdata = Path(pymupdf.get_tessdata())
    except Exception as exc:  # get_tessdata бросает RuntimeError, если tesseract не найден
        raise NoTextLayerError(
            "Для распознавания сканов нужен Tesseract: установите его "
            "(macOS: brew install tesseract tesseract-lang; Debian/Ubuntu: apt install "
            "tesseract-ocr tesseract-ocr-rus) или задайте TESSDATA_PREFIX"
        ) from exc
    missing = [
        lang for lang in cfg.language.split("+") if not (tessdata / f"{lang}.traineddata").exists()
    ]
    if missing:
        raise NoTextLayerError(
            f"В Tesseract нет данных языков: {', '.join(missing)} ({tessdata}). "
            "Установите их или поменяйте ocr.language в config/ingest.yaml"
        )
    from pymupdf4llm.ocr import tesseract_api

    return tesseract_api.exec_ocr


def read_pdf(
    path: Path, parser: MarkdownParser, *, ocr: OcrConfig | None = None
) -> tuple[list[RawBlock], int]:
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
        scanned = not any(page.get_text().strip() for page in doc)
        if scanned and ocr is None:
            raise NoTextLayerError(
                "В PDF нет текстового слоя (скан). Запустите с --ocr (нужен Tesseract) "
                "или сохраните документ как PDF с текстом, DOCX или Markdown"
            )
        ocr_kwargs: dict[str, Any] = {"use_ocr": False}
        if ocr is not None:
            ocr_kwargs = {
                "use_ocr": True,  # только страницы без текста: текстовый слой не трогаем
                "ocr_function": tesseract_ocr(ocr),
                "ocr_language": ocr.language,
                "ocr_dpi": ocr.dpi,
            }
        chunks = pymupdf4llm.to_markdown(
            doc,
            page_chunks=True,
            header=False,  # колонтитулы повторяются на каждой странице и только мешают
            footer=False,
            show_progress=False,
            **ocr_kwargs,
        )
    if ocr is not None and not any((c["text"] or "").strip() for c in chunks):
        raise NoTextLayerError("OCR не распознал текст: проверьте качество скана и язык")

    state = ParseState()
    blocks: list[RawBlock] = []
    for chunk in chunks:
        page_number = int(chunk["metadata"]["page_number"])
        text = normalize(chunk["text"] or "")
        blocks.extend(parser.parse(text, state, page=page_number, with_lines=False))
    return blocks, pages
