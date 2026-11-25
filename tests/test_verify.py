"""Проверка цитат и правила кодом: основа «находка без цитаты не попадает в отчёт»."""

import pytest

from reqcheck.config import lexicon
from reqcheck.schemas import Anchor
from reqcheck.schemas.document import Block
from reqcheck.stages.quotes import locate, similarity
from reqcheck.stages.rules import quote_window

TEXT = "Сервис передаёт push на отправку в течение минуты после получения события."


@pytest.mark.parametrize(
    "quote",
    [
        "передаёт push на отправку",
        "передает push на отправку",  # ё/е
        "«в течение минуты»",  # кавычки
        "в течение минуты",  # неразрывный пробел
        "**в течение минуты**",  # разметка
        "передаёт push на отправку в течение минуты после получения события",
    ],
)
def test_quote_found(quote: str) -> None:
    assert similarity(quote, TEXT) >= 90


@pytest.mark.parametrize(
    "quote",
    [
        "мгновенно отправляет уведомление",  # выдумано
        TEXT + " И ещё много текста, которого в блоке нет совсем.",  # длиннее текста
        "в",  # слишком коротко
    ],
)
def test_quote_rejected(quote: str) -> None:
    assert similarity(quote, TEXT) < 90


def test_hyphenated_pdf_text() -> None:
    from reqcheck.ingest.normalize import normalize

    assert similarity("получения события", normalize("после полу-\nчения события")) >= 90


def test_locate_prefers_given_blocks() -> None:
    a = Block(block_id="B-0001", kind="paragraph", text=TEXT, anchor=Anchor(line=1))
    b = Block(block_id="B-0002", kind="paragraph", text=TEXT, anchor=Anchor(line=9))
    assert locate("в течение минуты", [a, b], 90, prefer=[b]) is b
    assert locate("нет такого текста здесь", [a, b], 90) is None


def test_quote_window_is_verbatim() -> None:
    text = "Пользователь может быстро и удобно сохранить порог в настройках карты."
    start = text.index("быстро")
    q = quote_window(text, start, start + len("быстро"), 2)
    assert q == "Пользователь может быстро и удобно"
    assert q in text


def test_lexicon_matches_word_forms_not_substrings() -> None:
    vague = lexicon().vague.compiled()
    assert vague.search("Ответ приходит Быстрее")
    assert vague.search("и т.д.")
    assert not vague.search("предоставляет простоту")  # «простоту» не в списке форм
    assert not vague.search("максимум один push")
