"""Оркестратор этапов (docs/02-trd.md, §5). Пока каркас: этапы появятся по плану docs/07-plan.md."""

from dataclasses import dataclass

from reqcheck.formats import InputFile


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


def run(source: InputFile, options: Options) -> None:
    raise NotImplementedYet("конвейер проверки ещё не реализован: это каркас проекта")
