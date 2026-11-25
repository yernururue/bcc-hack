"""CLI: `python main.py <файл_требований> <файл_отчёта>` (docs/02-trd.md, §3)."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from reqcheck import pipeline
from reqcheck.errors import AuthError, ExitCode, ReqCheckError
from reqcheck.formats import SUPPORTED_HINT, detect_input, resolve_output
from reqcheck.settings import Settings
from reqcheck.version import agent_version

log = logging.getLogger("reqcheck")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reqcheck",
        description="Статическое ревью документа требований (ISO/IEC/IEEE 29148, IEEE 830).",
        epilog=f"Вход: {SUPPORTED_HINT}. Формат отчёта по умолчанию совпадает с форматом входа.",
    )
    p.add_argument("input", type=Path, help="файл требований")
    p.add_argument("output", type=Path, help="файл отчёта (.pdf, .md, .json или без расширения)")
    p.add_argument("--model", help="модель (по умолчанию LLM_MODEL из .env)")
    p.add_argument(
        "--max-cost", type=float, metavar="USD", help="лимит стоимости прогона в долларах"
    )
    p.add_argument("--no-cache", action="store_true", help="не читать кэш ответов LLM")
    p.add_argument("--json", type=Path, metavar="PATH", help="куда записать JSON-отчёт")
    p.add_argument("--dry-run", action="store_true", help="разобрать документ без вызовов LLM")
    p.add_argument(
        "--fail-on",
        choices=["blocker"],
        help="код выхода 10, если есть дефекты указанной серьёзности (для CI)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="подробные логи")
    return p


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    try:
        settings = settings or Settings()
        source = detect_input(args.input)
        target = resolve_output(source.format, args.output, args.json)
        options = pipeline.Options(
            model=args.model or settings.llm_model,
            max_cost_usd=args.max_cost if args.max_cost is not None else settings.llm_max_cost_usd,
            use_cache=not args.no_cache,
            dry_run=args.dry_run,
        )
        if options.max_cost_usd <= 0:
            raise ReqCheckError("--max-cost должен быть больше нуля")

        print(f"Документ: {source.path} ({source.format})", file=sys.stderr)
        report = f"{target.path} ({target.format}), JSON: {target.json_path}"
        print(f"Отчёт:    {report}", file=sys.stderr)
        print(
            f"Модель:   {options.model} · лимит ${options.max_cost_usd:.2f}"
            f" · агент {agent_version()}",
            file=sys.stderr,
        )

        if not options.dry_run and settings.llm_api_key is None:
            raise AuthError(
                "Нет ключа LLM. Создайте ключ на https://openrouter.ai/keys и впишите его "
                "в .env: LLM_API_KEY=... (пример — .env.example)"
            )

        outcome = pipeline.run(
            source,
            target,
            options,
            settings,
            progress=lambda msg: print(msg, file=sys.stderr),
        )
    except ReqCheckError as e:
        print(f"Ошибка: {e.message}", file=sys.stderr)
        return int(e.exit_code)
    except pipeline.NotImplementedYet as e:
        print(f"Не реализовано: {e}", file=sys.stderr)
        return int(ExitCode.ERROR)

    report = outcome.report
    if report is None:  # dry-run
        return int(ExitCode.OK)
    u = report.run.llm
    print(
        f"Готово:   {', '.join(str(p) for p in outcome.written)}\n"
        f"Расход:   вызовов {u.calls} (из кэша {u.cached_calls}), "
        f"{u.prompt_tokens} вход / {u.completion_tokens} выход, ${u.cost_usd:.4f}\n"
        f"Итог:     {report.verdict.reason}",
        file=sys.stderr,
    )
    if not report.run.complete:
        print(f"Внимание: отчёт неполный — {report.run.incomplete_reason}", file=sys.stderr)
    if outcome.budget_exceeded:
        return int(ExitCode.BUDGET)
    if args.fail_on == "blocker" and report.summary.by_severity.get("blocker", 0) > 0:
        return int(ExitCode.FAIL_ON)
    return int(ExitCode.OK)


if __name__ == "__main__":
    raise SystemExit(main())
