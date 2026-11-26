"""python -m reqcheck.eval --model ID [--baseline] | --dry-run | --compare ФАЙЛЫ.json

Только модели tier dev/cheap из config/models.yaml; сильные — с --allow-strong (согласие владельца).
"""

import argparse
import sys
from pathlib import Path

from reqcheck import pipeline
from reqcheck.config import thresholds
from reqcheck.errors import ReqCheckError
from reqcheck.eval import tables
from reqcheck.eval.matching import load_expected
from reqcheck.eval.runner import EvalResult, check_tier, result_path, run_model
from reqcheck.formats import detect_input
from reqcheck.ingest import ingest
from reqcheck.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
RESULTS = ROOT / "eval" / "results"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m reqcheck.eval", description=__doc__)
    p.add_argument("--model", action="append", default=[], help="ID модели (можно несколько)")
    p.add_argument("--baseline", action="store_true", help="baseline вместо конвейера")
    p.add_argument("--dry-run", action="store_true", help="только оценка стоимости, без вызовов")
    p.add_argument("--compare", nargs="+", type=Path, metavar="JSON", help="свести результаты")
    p.add_argument("--max-cost", type=float, default=0.08, metavar="USD", help="лимит на прогон")
    p.add_argument("--no-cache", action="store_true", help="не читать кэш (только по нужде)")
    p.add_argument("--allow-strong", action="store_true", help="разрешить tier strong (дорого)")
    p.add_argument("--fixtures", type=Path, default=FIXTURES)
    p.add_argument("--out", type=Path, default=RESULTS, help="куда писать JSON результатов")
    return p


def dry_run(models: list[str], fixtures: Path) -> None:
    expected = load_expected(fixtures / "expected.yaml")
    docs = [ingest(detect_input(fixtures / name)) for name in expected.fixtures]
    batch = thresholds().batch_size
    total = 0.0
    print("| Модель | Режим | Вызовов | Вход, k токенов | Выход, k токенов | Оценка, $ |")
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
            total += usd
            print(
                f"| {model} | {mode} | {sum(e.calls for e in ests)} "
                f"| {sum(e.tokens_in for e in ests) / 1000:.0f} "
                f"| {sum(e.tokens_out for e in ests) / 1000:.0f} | {usd:.3f} |"
            )
    print(f"\nИтого оценка: ≈ ${total:.3f} (кэш не учтён; у моделей с рассуждениями факт выше)")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.compare:
        results = [EvalResult.model_validate_json(p.read_text("utf-8")) for p in args.compare]
        print(tables.summary_table(results), end="\n\n")
        print(tables.kinds_table(results))
        if (best := tables.winner(results)) is not None:
            print(f"\nЛучший по правилу выбора: {best.model}")
        return 0

    if not args.model:
        print("Нужен --model ID (или --compare)", file=sys.stderr)
        return 2
    try:
        for model in args.model:
            check_tier(model, args.allow_strong)
        if args.dry_run:
            dry_run(args.model, args.fixtures)
            return 0
        settings = Settings()
        if settings.llm_api_key is None:
            print("Нет ключа LLM: LLM_API_KEY в .env", file=sys.stderr)
            return 4
        expected = load_expected(args.fixtures / "expected.yaml")
        mode = "baseline" if args.baseline else "pipeline"
        results = []
        for model in args.model:
            print(f"{model} · {mode}", file=sys.stderr)
            result = run_model(
                model,
                mode,
                expected,
                args.fixtures,
                settings,
                args.max_cost,
                use_cache=not args.no_cache,
                log=lambda m: print(m, file=sys.stderr),
            )
            path = result_path(args.out, result)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
            print(f"  → {path}", file=sys.stderr)
            results.append(result)
    except (ValueError, ReqCheckError) as exc:
        print(f"Ошибка: {getattr(exc, 'message', exc)}", file=sys.stderr)
        return 1

    print(tables.summary_table(results), end="\n\n")
    print(tables.kinds_table(results))
    for r in results:
        print(f"\nПропуски {r.model} ({r.mode}):\n{tables.misses(r)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
