"""Сопоставление находок с ожидаемыми дефектами и метрики (docs/02-trd.md, §10).

Находка попадает в ожидаемый дефект, если её категория совпадает с ожидаемой или с одним из
синонимов (`accept`) и место совпадает:
- режим цитат (`quotes`): partial_ratio цитат не ниже порога из thresholds.yaml;
- режим раздела (`sections`, для missing_topic и traceability, у которых нет места в тексте):
  хотя бы одно из мест находки (путь разделов её якорей) лежит в одном из названных разделов;
  `sections: ["*"]` — подходит любое место документа (например, «нет приоритетов вообще»).
Дефект и находка сопоставляются один к одному. Сети здесь нет.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator
from rapidfuzz import fuzz

from reqcheck.config import EvalRules
from reqcheck.schemas import Category
from reqcheck.schemas.common import Strict
from reqcheck.stages.quotes import match_key

Status = Literal["found", "wrong_category", "missed"]
Split = Literal["dev", "holdout"]
ANYWHERE = "*"


class ExpectedDefect(Strict):
    id: str
    category: Category
    accept: list[Category] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)  # режим раздела (см. описание модуля)
    min_quotes: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _one_mode(self) -> "ExpectedDefect":
        if bool(self.quotes) == bool(self.sections):
            raise ValueError(f"{self.id}: нужен ровно один режим — quotes или sections")
        return self


class FixtureSpec(Strict):
    # Путь к документу относительно файла разметки; по умолчанию — имя фикстуры рядом с ним
    path: str | None = None
    split: Split = "dev"
    defects: list[ExpectedDefect] = Field(default_factory=list)


class ExpectedFile(Strict):
    version: Literal[1]
    note: str | None = None
    fixtures: dict[str, FixtureSpec]


def load_expected(path: Path) -> ExpectedFile:
    return ExpectedFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def fixture_path(labels_file: Path, name: str, spec: FixtureSpec) -> Path:
    return (labels_file.parent / (spec.path or name)).resolve()


class EvalFinding(Strict):
    """Находка в общем виде: из конвейера и из baseline одинаково."""

    category: str
    severity: str
    quotes: list[str]
    sections: list[str] = Field(default_factory=list)  # пути разделов якорей находки
    explanation: str = ""
    # Для разбора шума: откуда находка (rule/llm), область (requirement/set), вердикт модели.
    source: str = ""
    scope: str = ""
    verdict: str = ""


class DefectResult(Strict):
    id: str
    category: Category
    status: Status
    matched_category: str | None = None  # категория находки, попавшей на место дефекта


class FixtureScore(Strict):
    fixture: str
    split: Split = "dev"
    defects: list[DefectResult] = Field(default_factory=list)
    findings_total: int = 0
    findings_matched: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)
    unmatched: list[EvalFinding] = Field(default_factory=list)

    @property
    def found(self) -> int:
        return sum(d.status == "found" for d in self.defects)

    @property
    def located(self) -> int:
        """Найдено на нужном месте, пусть и с другой категорией."""
        return sum(d.status != "missed" for d in self.defects)

    @property
    def recall(self) -> float | None:
        return self.found / len(self.defects) if self.defects else None


def quote_overlap(expected: str, actual: str, min_chars: int) -> float:
    """Пересечение двух цитат, 0–100: меньшая ищется внутри большей."""
    a, b = match_key(expected), match_key(actual)
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < min_chars:
        return 0.0
    if short in long_:
        return 100.0
    return float(fuzz.partial_ratio(short, long_))


def _in_sections(defect: ExpectedDefect, finding: EvalFinding) -> bool:
    if ANYWHERE in defect.sections:
        return True
    places = [match_key(s) for s in finding.sections]
    return any(match_key(want) in place for want in defect.sections for place in places)


def _coverage(defect: ExpectedDefect, finding: EvalFinding, rules: EvalRules) -> tuple[int, float]:
    """(сколько цитат дефекта покрыто находкой, сумма пересечений).

    Одна цитата находки покрывает не больше одной цитаты дефекта: иначе у двух похожих мест
    («до 5 адресов доставки» и «до 10 адресов доставки») хватило бы одной цитаты на оба.
    """
    scored = sorted(
        (
            (quote_overlap(eq, fq, rules.min_quote_chars), ei, fi)
            for ei, eq in enumerate(defect.quotes)
            for fi, fq in enumerate(finding.quotes)
        ),
        reverse=True,
    )
    if defect.sections:
        return (1, 100.0) if _in_sections(defect, finding) else (0, 0.0)
    used_e: set[int] = set()
    used_f: set[int] = set()
    covered, total = 0, 0.0
    for score, ei, fi in scored:
        if score < rules.quote_overlap:
            break
        if ei in used_e or fi in used_f:
            continue
        used_e.add(ei)
        used_f.add(fi)
        covered += 1
        total += score
    return covered, total


def score_fixture(
    name: str, spec: FixtureSpec, findings: list[EvalFinding], rules: EvalRules
) -> FixtureScore:
    """Сопоставление находок с разметкой одной фикстуры."""
    # Все пары «дефект — находка на нужном месте»; затем жадно по убыванию качества.
    pairs: list[tuple[int, float, int, int, bool]] = []
    for di, defect in enumerate(spec.defects):
        ok = {defect.category.value, *(c.value for c in defect.accept)}
        for fi, finding in enumerate(findings):
            covered, total = _coverage(defect, finding, rules)
            if covered >= defect.min_quotes:
                pairs.append((covered, total, di, fi, finding.category in ok))

    used_d: set[int] = set()
    used_f: set[int] = set()
    results: dict[int, DefectResult] = {}
    for strict in (True, False):
        for _covered, _total, di, fi, category_ok in sorted(
            pairs, key=lambda p: (-p[0], -p[1], p[2], p[3])
        ):
            if di in used_d or category_ok != strict:
                continue
            if strict and fi in used_f:
                continue
            defect = spec.defects[di]
            used_d.add(di)
            if strict:
                used_f.add(fi)
            results[di] = DefectResult(
                id=defect.id,
                category=defect.category,
                status="found" if strict else "wrong_category",
                matched_category=findings[fi].category,
            )

    ordered = [
        results.get(i) or DefectResult(id=d.id, category=d.category, status="missed")
        for i, d in enumerate(spec.defects)
    ]
    by_severity: dict[str, int] = {}
    for f in findings:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
    return FixtureScore(
        fixture=name,
        split=spec.split,
        defects=ordered,
        findings_total=len(findings),
        findings_matched=len(used_f),
        by_severity=by_severity,
        unmatched=[f for i, f in enumerate(findings) if i not in used_f],
    )


class Metrics(Strict):
    """Итоги по всем фикстурам одного прогона."""

    recall: float | None  # по всем дефектам всех фикстур
    recall_by_fixture: dict[str, float | None]
    recall_by_kind: dict[str, tuple[int, int]]  # категория → (найдено, ожидалось)
    located_recall: float | None
    precision: float | None  # доля находок на дефектных фикстурах, попавших в ожидаемые
    false_positives_clean: int  # все находки на фикстурах без дефектов
    false_positives_clean_major: int  # из них blocker и major


def compute_metrics(scores: list[FixtureScore]) -> Metrics:
    with_defects = [s for s in scores if s.defects]
    clean = [s for s in scores if not s.defects]
    kinds: dict[str, list[int]] = {}
    for s in with_defects:
        for d in s.defects:
            counts = kinds.setdefault(d.category.value, [0, 0])
            counts[1] += 1
            counts[0] += d.status == "found"
    expected_total = sum(len(s.defects) for s in with_defects)
    found_total = sum(s.found for s in with_defects)
    located_total = sum(s.located for s in with_defects)
    findings_total = sum(s.findings_total for s in with_defects)
    matched_total = sum(s.findings_matched for s in with_defects)
    return Metrics(
        recall=found_total / expected_total if expected_total else None,
        recall_by_fixture={s.fixture: s.recall for s in scores if s.defects},
        recall_by_kind={k: (v[0], v[1]) for k, v in sorted(kinds.items())},
        located_recall=located_total / expected_total if expected_total else None,
        precision=matched_total / findings_total if findings_total else None,
        false_positives_clean=sum(s.findings_total for s in clean),
        false_positives_clean_major=sum(
            s.by_severity.get("blocker", 0) + s.by_severity.get("major", 0) for s in clean
        ),
    )
