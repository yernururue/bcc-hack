"""Поиск цитаты в тексте документа: основа правила «находка без проверенной цитаты не попадает»."""

import re
from collections.abc import Iterable

from rapidfuzz import fuzz

from reqcheck.ingest.normalize import normalize
from reqcheck.schemas.document import Block

_DROP = str.maketrans({c: "" for c in "«»“”„\"'`*"})
_WS = re.compile(r"\s+")
MIN_QUOTE_CHARS = 4


def match_key(text: str) -> str:
    """Ключ сравнения: нормализация, регистр, ё→е, без кавычек и разметки, пробелы схлопнуты."""
    text = normalize(text).lower().replace("ё", "е").translate(_DROP)
    text = text.replace("‑", "-").replace("–", "-").replace("—", "-")
    return _WS.sub(" ", text).strip(" .,;:")


def similarity(quote: str, text: str) -> float:
    """Насколько цитата содержится в тексте, 0–100. Цитата длиннее текста не подходит."""
    q, t = match_key(quote), match_key(text)
    if len(q) < MIN_QUOTE_CHARS or not t:
        return 0.0
    if q in t:
        return 100.0
    if len(q) > len(t) * 1.15 + 5:
        return 0.0
    return float(fuzz.partial_ratio(q, t))


def locate(
    quote: str, blocks: Iterable[Block], threshold: float, prefer: Iterable[Block] = ()
) -> Block | None:
    """Блок, где цитата найдена (сначала среди prefer, затем во всём документе)."""
    for group in (list(prefer), list(blocks)):
        best: tuple[float, Block] | None = None
        for block in group:
            score = similarity(quote, block.text)
            if score >= threshold and (best is None or score > best[0]):
                best = (score, block)
                if score == 100.0:
                    break
        if best is not None:
            return best[1]
    return None


def locate_heading(quote: str, blocks: Iterable[Block], threshold: float) -> Block | None:
    """Заголовок раздела, совпадающий с цитатой: якорь для пробелов набора (missing_topic,
    traceability), у которых нет своего места в тексте. Цитата должна совпасть с названием
    раздела целиком (без номера), а не быть его кусочком."""
    q = match_key(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return None
    for block in blocks:
        if block.kind != "heading":
            continue
        title = match_key(block.text)
        bare = _NUM.sub("", title)
        if q in (title, bare) or (
            len(q) >= len(bare) * 0.8 and similarity(quote, title) >= threshold
        ):
            return block
    return None


_NUM = re.compile(r"^[\dа-яa-z]{0,3}[\d.)]+\s*")
