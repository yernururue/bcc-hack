"""Report → Markdown."""

import re

from jinja2 import Environment, PackageLoader, StrictUndefined

from reqcheck.config import Criteria, criteria
from reqcheck.render import context
from reqcheck.schemas import Finding, Report

_env = Environment(
    loader=PackageLoader("reqcheck.render", "templates"),
    autoescape=False,  # Markdown, не HTML
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def _cell(text: str) -> str:
    return context.inline(text).replace("|", "\\|")


def render_markdown(report: Report, crit: Criteria | None = None) -> str:
    ctx = context.build(report, crit or criteria())
    label_ref, place_list, quote_list = ctx["label_ref"], ctx["places"], ctx["quotes"]

    def places(f: Finding) -> str:
        ids = ", ".join(label_ref(i) for i in f.requirement_ids)
        return " ↔ ".join(place_list(f)) + (f" · {ids}" if ids else "")

    def quotes(f: Finding) -> str:
        return " ↔ ".join(f"«{q}»" for q in quote_list(f))

    def options(opts: list[str]) -> str:
        return "; ".join(f"{ctx['letter'](i)}) {o}" for i, o in enumerate(opts))

    ctx.update(
        cell=_cell,
        places=places,
        quotes=quotes,
        options=options,
        provider=ctx["provider"] if ctx["provider"] == "OpenRouter" else f"`{ctx['provider']}`",
        refs=lambda ids: ", ".join(ids) if ids else "нет",
        impacts=lambda ids: ", ".join(label_ref(i) for i in ids) if ids else "—",
    )
    text = _env.get_template("report.md.j2").render(**ctx)
    text = re.sub(r"[ \t]+\n", "\n", text)  # хвостовые пробелы
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
