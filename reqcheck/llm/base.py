"""Порт LLM: ядро зависит только от этого протокола (docs/03-architecture.md, §3).

Реализация для OpenAI-совместимых API (OpenRouter, шлюз банка) появится на этапе 8 плана.
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    cached: bool = False


@dataclass(frozen=True)
class LLMResult[M: BaseModel]:
    value: M
    usage: Usage


class LLMClient(Protocol):
    model: str

    def complete[M: BaseModel](
        self, *, system: str, user: str, schema: type[M], stage: str
    ) -> LLMResult[M]:
        """Один вызов модели со структурированным выводом по Pydantic-схеме `schema`."""
        ...
