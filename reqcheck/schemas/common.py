"""Общие типы: перечисления характеристик, категорий, серьёзности и якорь места в документе."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Characteristic(StrEnum):
    """Характеристики отдельного требования, ISO/IEC/IEEE 29148:2018 п. 5.2.5."""

    NECESSARY = "necessary"
    IMPLEMENTATION_FREE = "implementation_free"
    UNAMBIGUOUS = "unambiguous"
    COMPLETE = "complete"
    SINGULAR = "singular"
    FEASIBLE = "feasible"
    VERIFIABLE = "verifiable"
    CORRECT = "correct"
    CONFORMING = "conforming"


class Assessment(StrEnum):
    OK = "ok"
    VIOLATION = "violation"
    UNCLEAR = "unclear"


class Severity(StrEnum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


class Category(StrEnum):
    """Категории находок (docs/06-report-spec.md, §3)."""

    AMBIGUOUS = "ambiguous"
    UNVERIFIABLE = "unverifiable"
    INCOMPLETE = "incomplete"
    COMPOSITE = "composite"
    DESIGN_DECISION = "design_decision"
    INFEASIBLE = "infeasible"
    INCORRECT = "incorrect"
    UNNECESSARY = "unnecessary"
    NONCONFORMING = "nonconforming"
    CONTRADICTION = "contradiction"
    DUPLICATE = "duplicate"
    TERMINOLOGY = "terminology"
    MISSING_TOPIC = "missing_topic"
    TRACEABILITY = "traceability"


class RequirementKind(StrEnum):
    FUNCTIONAL = "functional"
    NFR = "nfr"
    DATA = "data"
    INTEGRATION = "integration"
    ACCEPTANCE = "acceptance"
    API = "api"
    OTHER = "other"


class Rect(Strict):
    """Прямоугольник на странице PDF в пунктах; начало координат — левый верхний угол.

    Нужен для подсветки цитаты в исходнике (P1, ADR-009).
    """

    page: int = Field(ge=1)
    x0: float
    y0: float
    x1: float
    y1: float


class Anchor(Strict):
    """Место в документе. page — 1-based для PDF, None для Markdown/TXT."""

    page: int | None = Field(default=None, ge=1)
    section_path: list[str] = Field(default_factory=list)
    item: str | None = None
    line: int | None = Field(default=None, ge=1)
    rects: list[Rect] = Field(default_factory=list)

    def label(self) -> str:
        parts: list[str] = []
        if self.page is not None:
            parts.append(f"стр. {self.page}")
        parts.extend(self.section_path)
        if self.item and (not self.section_path or self.section_path[-1] != self.item):
            parts.append(self.item)
        text = " › ".join(parts) if parts else "документ"
        if self.page is None and self.line is not None:
            text += f" (строка {self.line})"
        return text
