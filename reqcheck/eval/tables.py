"""Таблицы Markdown для презентации и выбор модели по правилу из ADR (docs/08-decisions.md)."""

from reqcheck.eval.runner import EvalResult


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f} %"


def _label(r: EvalResult) -> str:
    return r.model if r.mode == "pipeline" else f"{r.model} (baseline)"


def rank_key(r: EvalResult) -> tuple[float, int, float]:
    """Правило выбора: recall на defects, затем ложные на clean, затем цена."""
    recall = r.metrics.recall_by_fixture.get("defects.pdf") or 0.0
    return (-recall, r.metrics.false_positives_clean, r.cost_usd)


def summary_table(results: list[EvalResult]) -> str:
    rows = sorted(results, key=rank_key)
    head = (
        "| Модель | Recall defects | Recall contradictions | Recall всего | Точность "
        "| Ложные на clean (из них blocker/major) | Стоимость, $ | Время, с |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|\n"
    )
    lines = []
    for r in rows:
        m = r.metrics
        lines.append(
            f"| {_label(r)} | {_pct(m.recall_by_fixture.get('defects.pdf'))} "
            f"| {_pct(m.recall_by_fixture.get('contradictions.pdf'))} | {_pct(m.recall)} "
            f"| {_pct(m.precision)} | {m.false_positives_clean} ({m.false_positives_clean_major}) "
            f"| {r.cost_usd:.4f}{'*' if r.cached else ''} | {r.seconds:.0f} |"
        )
    text = head + "\n".join(lines)
    if any(r.cached for r in results):
        text += "\n\n\\* часть ответов взята из кэша: стоимость и время занижены."
    unfinished = [r for r in results if not all(f.complete for f in r.fixtures)]
    if unfinished:
        text += "\n\nНеполные прогоны: " + ", ".join(_label(r) for r in unfinished) + "."
    return text


def kinds_table(results: list[EvalResult]) -> str:
    rows = sorted(results, key=rank_key)
    kinds = sorted({k for r in rows for k in r.metrics.recall_by_kind})
    head = "| Вид дефекта | " + " | ".join(_label(r) for r in rows) + " |\n"
    head += "|---|" + "---:|" * len(rows) + "\n"
    lines = []
    for kind in kinds:
        cells = []
        for r in rows:
            found, total = r.metrics.recall_by_kind.get(kind, (0, 0))
            cells.append(f"{found}/{total}")
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
