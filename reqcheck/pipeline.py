"""Оркестратор этапов (docs/02-trd.md, §5). Пока каркас: этапы появятся по плану docs/07-plan.md."""

from collections.abc import Callable
from dataclasses import dataclass

from reqcheck.formats import InputFile
from reqcheck.ingest import ingest


@dataclass(frozen=True)
class Options:
    model: str
    max_cost_usd: float
    use_cache: bool = True
    dry_run: bool = False


STAGES = (
    "ingest",
    "extract",
    "check_each",
    "check_set",
    "verify",
    "questions",
    "verdict",
    "render",
)


class NotImplementedYet(NotImplementedError):
    """Этап ещё не реализован (каркас)."""


def _silent(_: str) -> None:
    pass


def run(source: InputFile, options: Options, progress: Callable[[str], None] = _silent) -> None:
    doc = ingest(source)
    numbered = sum(1 for b in doc.blocks if b.anchor.item)
    pages = f", страниц: {doc.pages}" if doc.pages else ""
    progress(f"Разбор:   блоков {len(doc.blocks)}, с номером пункта {numbered}{pages}")
    if options.dry_run:
        return
    raise NotImplementedYet("проверка требований ещё не реализована: готов только разбор документа")
