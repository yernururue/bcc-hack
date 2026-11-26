"""Таблицы Markdown для презентации и выбор модели по правилу из ADR (docs/08-decisions.md)."""

from statistics import mean

from reqcheck.eval.runner import EvalResult


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f} %"


def label(r: EvalResult) -> str:
    if r.mode == "pipeline":
        return r.model
    extra = f", {r.variant}" if r.variant else ""
    return f"{r.model} ({r.mode}{extra})"


def rank_key(r: EvalResult) -> tuple[float, int, float]:
    """Правило выбора: recall, затем ложные на чистых документах, затем цена."""
    return (-(r.metrics.recall or 0.0), r.metrics.false_positives_clean, r.cost_usd)


def summary_table(results: list[EvalResult]) -> str:
    """Один прогон на строку (старый формат: по фикстурам defects/contradictions)."""
    return repeat_table(results)


def _spread(values: list[float], fmt) -> str:
    if not values:
        return "—"
    if len(values) == 1:
        return fmt(values[0])
    return f"{fmt(mean(values))} ({fmt(min(values))}–{fmt(max(values))})"


def _pct_num(x: float) -> str:
    return f"{x * 100:.0f} %"


def _int(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def groups(results: list[EvalResult]) -> dict[str, list[EvalResult]]:
    out: dict[str, list[EvalResult]] = {}
    for r in results:
        out.setdefault(label(r), []).append(r)
    return out


def repeat_table(results: list[EvalResult]) -> str:
    """Среднее и разброс (мин–макс) по повторам: recall по выборкам dev/holdout, ложные на чистых
    документах, точность, стоимость."""
    head = (
        "| Вариант | Повторов | Recall dev | Recall holdout | Ложные на чистых dev "
        "| Ложные на чистых holdout | Из них blocker/major (все) | Точность | Стоимость, $ |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    )

    def split_val(r: EvalResult, split: str, attr: str):
        m = r.metrics_by_split.get(split)
        return None if m is None else getattr(m, attr)

    rows = []
    for name, rs in sorted(
        groups(results).items(), key=lambda kv: -mean(r.metrics.recall or 0 for r in kv[1])
    ):

        def vals(split: str, attr: str, rs: list[EvalResult] = rs) -> list[float]:
            return [v for r in rs if (v := split_val(r, split, attr)) is not None]

        cached = any(r.cached for r in rs)
        precision = [r.metrics.precision for r in rs if r.metrics.precision is not None]
        cost = _spread([r.cost_usd for r in rs], lambda x: f"{x:.4f}")
        rows.append(
            f"| {name} | {len(rs)} "
            f"| {_spread(vals('dev', 'recall'), _pct_num)} "
            f"| {_spread(vals('holdout', 'recall'), _pct_num)} "
            f"| {_spread(vals('dev', 'false_positives_clean'), _int)} "
            f"| {_spread(vals('holdout', 'false_positives_clean'), _int)} "
            f"| {_spread([r.metrics.false_positives_clean_major for r in rs], _int)} "
            f"| {_spread(precision, _pct_num)} "
            f"| {cost}{'*' if cached else ''} |"
        )
    text = head + "\n".join(rows)
    if any(r.cached for r in results):
        text += "\n\n\\* часть ответов взята из кэша: стоимость занижена."
    unfinished = sorted({label(r) for r in results if not all(f.complete for f in r.fixtures)})
    if unfinished:
        text += "\n\nНеполные прогоны: " + ", ".join(unfinished) + "."
    return text


def size_table(results: list[EvalResult]) -> str:
    """Recall по документам (среднее по повторам): где конвейер выигрывает у baseline."""
    names: dict[str, tuple[int, str]] = {}
    for r in results:
        for f in r.fixtures:
            names[f.score.fixture] = (f.blocks, f.score.split)
    gs = groups(results)
    head = "| Документ | Блоков | Выборка | " + " | ".join(gs) + " |\n"
    head += "|---|---:|---|" + "---:|" * len(gs) + "\n"
    lines = []
    for name, (blocks, split) in sorted(names.items(), key=lambda kv: kv[1][0]):
        cells = []
        for rs in gs.values():
            scores = [f.score for r in rs for f in r.fixtures if f.score.fixture == name]
            if not scores:
                cells.append("—")
            elif scores[0].defects:
                cells.append(_pct_num(mean(s.recall or 0 for s in scores)))
            else:
                cells.append(f"ложных {_int(mean(s.findings_total for s in scores))}")
        lines.append(f"| {name} | {blocks} | {split} | " + " | ".join(cells) + " |")
    return head + "\n".join(lines)


def kinds_table(results: list[EvalResult]) -> str:
    gs = groups(results)
    kinds = sorted({k for r in results for k in r.metrics.recall_by_kind})
    head = "| Вид дефекта | " + " | ".join(gs) + " |\n"
    head += "|---|" + "---:|" * len(gs) + "\n"
    lines = []
    for kind in kinds:
        cells = []
        for rs in gs.values():
            found = [r.metrics.recall_by_kind.get(kind, (0, 0)) for r in rs]
            total = found[0][1]
            cells.append(f"{_int(mean(f for f, _ in found))}/{total}")
        lines.append(f"| {kind} | " + " | ".join(cells) + " |")
    return head + "\n".join(lines)


def misses(result: EvalResult) -> str:
    """Какие дефекты не найдены и какие находки не попали ни в один дефект."""
    out = []
    for run in result.fixtures:
        for d in run.score.defects:
            if d.status != "found":
                extra = f", категория находки: {d.matched_category}" if d.matched_category else ""
                out.append(f"- {run.score.fixture} · {d.id}: {d.status}{extra}")
    return "\n".join(out) or "- все дефекты найдены"


def winner(results: list[EvalResult]) -> EvalResult | None:
    pipe = [r for r in results if r.mode == "pipeline"]
    return min(pipe, key=rank_key) if pipe else None
