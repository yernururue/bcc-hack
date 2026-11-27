"""Загрузка конфигурации из config/*.yaml: эвристики и пороги живут там, а не в коде."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from reqcheck.schemas.common import Category, Characteristic, RequirementKind, Severity

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_yaml(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    path = (config_dir or CONFIG_DIR) / name
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: ожидался словарь верхнего уровня")
    return data


class IngestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_patterns: list[str] = Field(min_length=1)
    ordered_item_label: str = "п. {n}"
    table_item_max_len: int = Field(default=40, ge=1)
    drop_single_h1_title: bool = True

    @field_validator("item_patterns")
    @classmethod
    def _patterns_compile(cls, value: list[str]) -> list[str]:
        for pattern in value:
            if "(?P<id>" not in pattern:
                raise ValueError(f"в шаблоне нет группы id: {pattern}")
            re.compile(pattern)
        return value

    def compiled_item_patterns(self) -> list[re.Pattern[str]]:
        return [re.compile(p) for p in self.item_patterns]


@lru_cache
def ingest_config() -> IngestConfig:
    return IngestConfig.model_validate(load_yaml("ingest.yaml"))


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    tier: Literal["dev", "cheap", "strong"]
    price_in: float = Field(ge=0)  # $ за 1M входных токенов
    price_out: float = Field(ge=0)
    temperature: bool = False
    reasoning: Literal["on", "off"] | None = None
    # Уровень рассуждений OpenRouter (reasoning.effort); "none" отключает их у моделей, где
    # рассуждения включены по умолчанию (поле reasoning.default_effort в /api/v1/models)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    models: list[ModelInfo]
    test_tiers: list[str] = Field(default_factory=lambda: ["dev", "cheap"])

    def get(self, model_id: str) -> ModelInfo | None:
        return next((m for m in self.models if m.id == model_id), None)


class VerdictRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_development_if_blockers_at_least: int = Field(ge=1)
    caution_if_major_at_least: int = Field(ge=1)
    block_testing_if_unverifiable_at_least: int = Field(ge=1)
    block_testing_if_no_acceptance: bool


class FindingsFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_per_requirement: int = Field(ge=1)
    # «Неясно» не строже этой серьёзности в отчёт не выводим (остаётся в debug)
    drop_unclear_up_to: Severity
    # Лимит находок набора по категориям; лишние (менее серьёзные) убираются в debug
    max_set_per_category: dict[Category, int] = Field(default_factory=dict)


class EvalRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quote_overlap: float = Field(ge=0, le=100)
    min_quote_chars: int = Field(ge=1)


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: VerdictRules
    findings_filter: FindingsFilter
    eval: EvalRules
    verify: dict[str, float]
    check_each: dict[str, int]
    check_set: dict[str, int]
    estimate: dict[str, float] = Field(default_factory=dict)
    report: dict[str, int]
    default_severity: dict[Category, Severity]
    # Модель может повысить серьёзность выше умолчания, но не выше этого потолка
    max_severity: dict[Category, Severity] = Field(default_factory=dict)

    @property
    def quote_min_similarity(self) -> float:
        return float(self.verify["quote_min_similarity"])

    @property
    def batch_size(self) -> int:
        return int(self.check_each["batch_size"])


class CharacteristicInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    short: str
    category: Category
    standard_ref: str
    definition: str


class SetSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    categories: list[Category]


class Criteria(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    characteristics: dict[Characteristic, CharacteristicInfo]
    set_sections: list[SetSection]
    category_names: dict[Category, str]
    kind_names: dict[RequirementKind, str]


class LexiconRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Category
    characteristic: Characteristic
    explanation: str
    patterns: list[str] = Field(min_length=1)

    def compiled(self) -> re.Pattern[str]:
        joined = "|".join(f"(?:{p})" for p in self.patterns)
        return re.compile(rf"(?<!\w)(?:{joined})(?!\w)", re.IGNORECASE)


class Lexicon(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    vague: LexiconRule
    tbd: LexiconRule
    quote_context_words: int = Field(default=3, ge=0)

    def rules(self) -> list[LexiconRule]:
        return [self.vague, self.tbd]


@lru_cache
def models_config() -> ModelsConfig:
    return ModelsConfig.model_validate(load_yaml("models.yaml"))


@lru_cache
def thresholds() -> Thresholds:
    return Thresholds.model_validate(load_yaml("thresholds.yaml"))


@lru_cache
def criteria() -> Criteria:
    return Criteria.model_validate(load_yaml("criteria.yaml"))


@lru_cache
def lexicon() -> Lexicon:
    return Lexicon.model_validate(load_yaml("lexicon.yaml"))
