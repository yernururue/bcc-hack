"""Вырожденный ответ модели: «находки» без цитат и без пояснения (ADR-012).

deepseek на check_set в 5 из 12 вызовов возвращал одну пустую находку вместо списка. Код такую
находку отбрасывает (нет цитаты), и документ молча терял все противоречия, а отчёт оставался
«полным». Теперь такой ответ запрашивается заново один раз (с поправкой в промпте — это другой
ключ кэша, повторный прогон воспроизводит обе попытки), а повторная пустота — ошибка этапа, и
отчёт помечается неполным.
"""

from collections.abc import Callable, Sequence
from typing import Protocol

from reqcheck import prompts
from reqcheck.llm.openai_compat import LLMCallError


class _Finding(Protocol):
    quotes: list[str]
    explanation: str


def is_empty(findings: Sequence[_Finding]) -> bool:
    """Все находки пустые (нет ни цитат, ни пояснения). Пустой список — нормальный ответ."""
    return bool(findings) and all(
        not any(q.strip() for q in f.quotes) and not f.explanation.strip() for f in findings
    )


def with_retry[F: _Finding](ask: Callable[[str], list[F]], user: str, stage: str) -> list[F]:
    """`ask(user)` → находки; при вырожденном ответе — один повтор с поправкой."""
    found = ask(user)
    if not is_empty(found):
        return found
    found = ask(user + "\n\n" + prompts.load("empty_retry"))
    if is_empty(found):
        raise LLMCallError(f"{stage}: модель дважды вернула находки без цитат и пояснений")
    return found
