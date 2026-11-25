"""Проверки кодом по словарям config/lexicon.yaml: расплывчатые слова, TBD-пометки.

Детерминированы и бесплатны; цитата — дословное окно текста вокруг совпадения.
"""

import re

from reqcheck.config import Criteria, Lexicon, LexiconRule, Thresholds
from reqcheck.schemas import Finding, Requirement

_WORD = re.compile(r"\S+")


def quote_window(text: str, start: int, end: int, context_words: int) -> str:
    """Фрагмент text вокруг [start, end) с context_words словами по сторонам — дословно."""
    words = list(_WORD.finditer(text))
    first = next(i for i, w in enumerate(words) if w.end() > start)
    last = next(i for i in range(len(words) - 1, -1, -1) if words[i].start() < end)
    a = max(0, first - context_words)
    b = min(len(words) - 1, last + context_words)
    return text[words[a].start() : words[b].end()]


def _finding(
    req: Requirement,
    rule: LexiconRule,
    matches: list[re.Match[str]],
    context_words: int,
    thresholds: Thresholds,
    criteria: Criteria,
) -> Finding:
    first = matches[0]
    found = list(dict.fromkeys(m.group(0) for m in matches))
    return Finding(
        finding_id="F-000",
        scope="requirement",
        requirement_ids=[req.report_id],
        characteristic=rule.characteristic,
        category=rule.category,
        severity=thresholds.default_severity[rule.category],
        verdict="violation",
        quote=quote_window(req.quote, first.start(), first.end(), context_words),
        anchor=req.anchor,
        explanation=rule.explanation.format(match="», «".join(found)),
        source="rule",
        standard_ref=criteria.characteristics[rule.characteristic].standard_ref,
    )


def run(
    requirements: list[Requirement], lexicon: Lexicon, thresholds: Thresholds, criteria: Criteria
) -> list[Finding]:
    findings: list[Finding] = []
    compiled = [(rule, rule.compiled()) for rule in lexicon.rules()]
    for req in requirements:
        for rule, pattern in compiled:
            matches = list(pattern.finditer(req.quote))
            if matches:
                findings.append(
                    _finding(req, rule, matches, lexicon.quote_context_words, thresholds, criteria)
                )
    return findings
