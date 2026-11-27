"""Библиотека: `validate(data, filename, options, on_event)` → Report (docs/04-integration.md, §2).

Тот же конвейер, что у CLI и сервера: файл пишется во временный каталог (формат определяется
по имени), отчёт возвращается объектом; `report.save(path)` пишет его в формате по расширению.
"""

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from reqcheck import pipeline
from reqcheck.errors import AuthError, InputError
from reqcheck.formats import detect_input, resolve_output
from reqcheck.llm.base import LLMClient
from reqcheck.schemas import Report
from reqcheck.settings import Settings


@dataclass(frozen=True)
class Event:
    """Событие прогресса: этап, состояние (active|done|error), доля выполнения 0–1, пояснение."""

    stage: str
    state: str
    progress: float
    note: str | None = None


OnEvent = Callable[[Event], None]


@dataclass(frozen=True)
class Options:
    """Параметры проверки; None — значение из настроек (.env: LLM_MODEL, LLM_MAX_COST_USD)."""

    model: str | None = None
    max_cost_usd: float | None = None
    use_cache: bool = True


def validate(
    data: bytes | str | Path,
    filename: str | None = None,
    options: Options | None = None,
    on_event: OnEvent | None = None,
    *,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
) -> Report:
    """Проверяет документ требований и возвращает отчёт.

    `data` — содержимое файла (bytes или текст Markdown) или путь к нему; `filename` нужен для
    bytes/текста: по расширению определяется формат (.pdf, .md, .txt). `llm` подменяет клиента
    модели (тесты, собственный шлюз); без него нужен ключ LLM_API_KEY в настройках.
    """
    settings = settings or Settings()
    options = options or Options()
    run_options = pipeline.Options(
        model=options.model or settings.llm_model,
        max_cost_usd=options.max_cost_usd
        if options.max_cost_usd is not None
        else settings.llm_max_cost_usd,
        use_cache=options.use_cache,
    )
    if run_options.max_cost_usd <= 0:
        raise InputError("max_cost_usd должен быть больше нуля")
    if llm is None and settings.llm_api_key is None:
        raise AuthError("Нет ключа LLM: задайте LLM_API_KEY в .env или переменных окружения")

    with tempfile.TemporaryDirectory(prefix="reqcheck-") as tmp:
        if isinstance(data, Path):
            path = data
        else:
            if not filename:
                raise InputError("Для содержимого файла нужно имя (filename) с расширением")
            path = Path(tmp) / Path(filename).name
            if isinstance(data, str):
                path.write_text(data, encoding="utf-8")
            else:
                path.write_bytes(data)
        source = detect_input(path)
        target = resolve_output(source.format, Path(tmp) / "out" / "report.json")

        def on_stage(stage: str, state: str, note: str | None) -> None:
            if on_event is None:
                return
            index = pipeline.STAGES.index(stage)
            done = index + (1 if state in ("done", "error") else 0)
            on_event(Event(stage, state, round(done / len(pipeline.STAGES), 3), note))

        outcome = pipeline.run(source, target, run_options, settings, llm=llm, on_stage=on_stage)
    assert outcome.report is not None
    return outcome.report
