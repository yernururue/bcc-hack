"""Промпты лежат в *.md рядом; версия — в первой строке и попадает в ключ кэша вместе с текстом."""

from functools import cache
from importlib.resources import files


@cache
def load(name: str) -> str:
    return (files(__package__) / f"{name}.md").read_text(encoding="utf-8")


def render(name: str, **values: str) -> str:
    """Подставляет {ключ}. str.format не используем: в данных бывают фигурные скобки (JSON)."""
    text = load(name)
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text
