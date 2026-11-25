"""Загрузка конфигурации из config/*.yaml: эвристики и пороги живут там, а не в коде."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
