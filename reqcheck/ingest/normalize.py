"""Нормализация текста: одинаковая для разбора документа и для проверки цитат (ADR-004)."""

import re
import unicodedata

# NFKC не используем: он превращает «№» в «No» и ломает цитаты. Заменяем точечно.
_CHAR_MAP = str.maketrans(
    {
        "­": "",  # мягкий перенос
        "​": "",  # пробел нулевой ширины
        "‌": "",
        "‍": "",
        "﻿": "",  # BOM
        " ": " ",  # неразрывный пробел
        " ": " ",
        " ": " ",  # тонкая шпация (разряды «1 000»)
        " ": " ",  # узкий неразрывный пробел
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬅ": "st",
        "ﬆ": "st",
    }
)

# Перенос слова по слогам в PDF: «требова-\nния» → «требования» (только между строчными буквами).
_HYPHEN_BREAK = re.compile(r"(?<=[a-zа-яё])-\n(?=[a-zа-яё])")
_SPACES = re.compile(r"[ \t]+")


def normalize(text: str) -> str:
    """Канонический вид текста без потери смысла: NFC, пробелы, лигатуры, переносы."""
    text = unicodedata.normalize("NFC", text).translate(_CHAR_MAP)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_BREAK.sub("", text)
    return text


def squash_spaces(text: str) -> str:
    return _SPACES.sub(" ", text).strip()
