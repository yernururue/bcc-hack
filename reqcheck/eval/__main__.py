"""python -m reqcheck.eval --model ID [--baseline | --both] [--repeat N] | --compare JSON...

Только модели tier dev/cheap из config/models.yaml; сильные — с --allow-strong (согласие владельца).
Разметка: tests/fixtures/expected.yaml (общие фикстуры) и, по --labels, другие файлы (например,
eval/labels/*.yaml). Повторы (--repeat N): первый — обычный кэш, следующие — с солью ключа кэша,
поэтому повтор действительно вызывает модель, а повторный запуск eval те же ответы берёт из кэша.
"""

import argparse
import sys
from pathlib import Path

from reqcheck import pipeline
from reqcheck.config import thresholds
from reqcheck.errors import ReqCheckError
from reqcheck.eval import tables
from reqcheck.eval.matching import ExpectedFile, fixture_path, load_expected
from reqcheck.eval.runner import (
    EvalResult,
    check_tier,
    hybrid,
    result_path,
    run_model,
)
from reqcheck.formats import detect_input
from reqcheck.ingest import ingest
from reqcheck.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
RESULTS = ROOT / "eval" / "results"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m reqcheck.eval", description=__doc__)
    p.add_argument("--model", action="append", default=[], help="ID модели (можно несколько)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--baseline", action="store_true", help="baseline вместо конвейера")
    mode.add_argument(
        "--both", action="store_true", help="конвейер и baseline, плюс hybrid из них (без вызовов)"
    )
    p.add_argument(
        "--hybrid-at",
        type=int,
        action="append",
        default=[],
        metavar="БЛОКОВ",
        help="порог hybrid: документ до N блоков — baseline, больше — конвейер (можно несколько)",
    )
    p.add_argument("--repeat", type=int, default=1, metavar="N", help="число повторов прогона")
    p.add_argument("--dry-run", action="store_true", help="только оценка стоимости, без вызовов")
    p.add_argument("--compare", nargs="+", type=Path, metavar="JSON", help="свести результаты")
    p.add_argument("--max-cost", type=float, default=0.08, metavar="USD", help="лимит на прогон")
    p.add_argument("--no-cache", action="store_true", help="не читать кэш (только по нужде)")
    p.add_argument("--allow-strong", action="store_true", help="разрешить tier strong (дорого)")
    p.add_argument("--fixtures", type=Path, default=FIXTURES, help="каталог с expected.yaml")
    p.add_argument(
        "--labels", type=Path, action="append", default=[], help="доп. файл разметки (yaml)"
    )
    p.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    p.add_argument("--only", action="append", default=[], metavar="ИМЯ", help="только фикстура")
    p.add_argument("--tag", default="", help="метка в имени файлов результатов")
    p.add_argument("--out", type=Path, default=RESULTS, help="куда писать JSON результатов")
    return p


def load_labels(fixtures: Path, extra: list[Path]) -> ExpectedFile:
    """Все файлы разметки в один: пути документов становятся абсолютными."""
    merged: dict = {}
    for path in [fixtures / "expected.yaml", *extra]:
        labels = load_expected(path)
        for name, spec in labels.fixtures.items():
            if name in merged:
                raise ValueError(f"фикстура {name} размечена дважды")
            merged[name] = spec.model_copy(update={"path": str(fixture_path(path, name, spec))})
    return ExpectedFile(version=1, fixtures=merged)


def selected(labels: ExpectedFile, split: str, only: list[str]) -> set[str]:
    return {
        name
        for name, spec in labels.fixtures.items()
        if (split == "all" or spec.split == split) and (not only or name in only)
    }


def dry_run(models: list[str], labels: ExpectedFile, names: set[str], repeat: int) -> None:
    docs = [
        ingest(detect_input(fixture_path(Path("/"), n, s)))
        for n, s in labels.fixtures.items()
        if n in names
    ]
    batch = thresholds().batch_size
    total = 0.0
    print(f"Документов: {len(docs)}, повторов: {repeat}")
    print("| Модель | Режим | Вызовов | Вход, k токенов | Выход, k токенов | Оценка за повтор, $ |")
    print("|---|---|---:|---:|---:|---:|")
    for model in models:
        for mode in ("pipeline", "baseline"):
            ests = [
                pipeline.estimate_numbers(d, model, batch)
                if mode == "pipeline"
                else pipeline.estimate_baseline(d, model)
                for d in docs
            ]
            usd = sum(e.usd or 0.0 for e in ests)
            total += usd * repeat
            print(
                f"| {model} | {mode} | {sum(e.calls for e in ests)} "
                f"| {sum(e.tokens_in for e in ests) / 1000:.0f} "
                f"| {sum(e.tokens_out for e in ests) / 1000:.0f} | {usd:.3f} |"
            )
    print(
        f"\nИтого оценка на оба режима × {repeat}: ≈ ${total:.3f} "
        "(кэш не учтён; поправка — config/thresholds.yaml, estimate)"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.compare:
        results = [EvalResult.model_validate_json(p.read_text("utf-8")) for p in args.compare]
        print(tables.repeat_table(results), end="\n\n")
        print(tables.kinds_table(results))
        return 0

    if not args.model:
        print("Нужен --model ID (или --compare)", file=sys.stderr)
        return 2
    try:
        for model in args.model:
            check_tier(model, args.allow_strong)
        labels = load_labels(args.fixtures, args.labels)
        names = selected(labels, args.split, args.only)
        if not names:
            print("Нет фикстур под фильтр --split/--only", file=sys.stderr)
            return 2
        if args.dry_run:
            dry_run(args.model, labels, names, args.repeat)
            return 0
        settings = Settings()
        if settings.llm_api_key is None:
            print("Нет ключа LLM: LLM_API_KEY в .env", file=sys.stderr)
            return 4
        modes = (
            ["pipeline", "baseline"] if args.both else ["baseline" if args.baseline else "pipeline"]
        )
        results: list[EvalResult] = []
        spent = 0.0
        for model in args.model:
            for rep in range(1, args.repeat + 1):
                by_mode: dict[str, EvalResult] = {}
                for mode in modes:
                    print(f"{model} · {mode} · повтор {rep}", file=sys.stderr)
                    result = run_model(
                        model,
                        mode,  # type: ignore[arg-type]
                        labels,
                        args.fixtures,
                        settings,
                        max(0.0, args.max_cost - spent),
                        use_cache=not args.no_cache,
                        log=lambda m: print(m, file=sys.stderr),
                        repeat=rep,
                        only=names,
                        labels_file=Path("/"),
                    )
                    spent += result.cost_usd
                    by_mode[mode] = result
                    results.append(result)
                    _save(args.out, result, args.tag)
                    print(f"  итого потрачено ${spent:.4f}", file=sys.stderr)
                if args.both:
                    for at in args.hybrid_at:
                        h = hybrid(by_mode["pipeline"], by_mode["baseline"], at)
                        results.append(h)
                        _save(args.out, h, f"{args.tag}-at{at}".strip("-"))
    except (ValueError, ReqCheckError) as exc:
        print(f"Ошибка: {getattr(exc, 'message', exc)}", file=sys.stderr)
        return 1

    print(tables.repeat_table(results), end="\n\n")
    print(tables.kinds_table(results))
    for r in results:
        if r.repeat == 1 and r.mode != "hybrid":
            print(f"\nПропуски {tables.label(r)} (повтор 1):\n{tables.misses(r)}")
    return 0


def _save(out: Path, result: EvalResult, tag: str) -> None:
    path = result_path(out, result, tag)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    print(f"  → {path}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
