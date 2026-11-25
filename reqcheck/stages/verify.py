"""Этап verify: каждая находка должна опираться на цитату, найденную кодом в тексте (ADR-004).

Здесь же итоговая серьёзность (по умолчанию из thresholds.yaml, модель может только повысить)
и согласование оценок характеристик с подтверждёнными находками.
"""

from dataclasses import dataclass, field

from reqcheck.config import Criteria, FindingsFilter, Thresholds
from reqcheck.schemas import (
    Assessment,
    Category,
    Characteristic,
    Finding,
    Requirement,
    RequirementKind,
    Severity,
)
from reqcheck.schemas.document import Block, Document
from reqcheck.stages.check_each import CheckEachOutput
from reqcheck.stages.check_set import SetFinding
from reqcheck.stages.extract import candidate_blocks
from reqcheck.stages.quotes import locate, similarity

SEVERITY_RANK = {Severity.BLOCKER: 0, Severity.MAJOR: 1, Severity.MINOR: 2}


def final_severity(
    category: Category,
    proposed: Severity,
    kinds: list[RequirementKind],
    thresholds: Thresholds,
) -> Severity:
    base = thresholds.default_severity[category]
    if category == Category.UNVERIFIABLE and RequirementKind.FUNCTIONAL not in kinds:
        base = Severity.MAJOR  # blocker — только для функциональных (docs/06-report-spec.md §3)
    return min(base, proposed, key=SEVERITY_RANK.__getitem__)


@dataclass
class VerifyOutput:
    findings: list[Finding] = field(default_factory=list)
    dropped: list[Finding] = field(default_factory=list)


def _block_of(req: Requirement, blocks: list[Block]) -> list[Block]:
    """Блок(и) требования: тот, где лежит его цитата."""
    found = locate(req.quote, blocks, 99.0)
    return [found] if found else []


def requirement_findings(
    reqs: list[Requirement],
    judged: CheckEachOutput,
    rule_findings: list[Finding],
    doc: Document,
    thresholds: Thresholds,
    criteria: Criteria,
) -> VerifyOutput:
    blocks = candidate_blocks(doc)
    threshold = thresholds.quote_min_similarity
    out = VerifyOutput()
    by_id = {r.report_id: r for r in reqs}

    for req in reqs:
        item = judged.items.get(req.report_id)
        if item is None:
            continue
        home = _block_of(req, blocks)
        for jf in item.findings:
            info = criteria.characteristics[jf.characteristic]
            finding = Finding(
                finding_id="F-000",
                scope="requirement",
                requirement_ids=[req.report_id],
                characteristic=jf.characteristic,
                category=info.category,
                severity=final_severity(info.category, jf.severity, [req.kind], thresholds),
                verdict=jf.verdict,
                quote=jf.quote.strip(),
                anchor=req.anchor,
                explanation=jf.explanation.strip(),
                suggestion=(jf.suggestion or "").strip() or None,
                source="llm",
                standard_ref=info.standard_ref,
            )
            # Цитата должна быть в тексте самого требования (или его блока).
            located = locate(finding.quote, home, threshold) if home else None
            if similarity(finding.quote, req.quote) >= threshold or located:
                out.findings.append(finding.model_copy(update={"verified": True}))
            else:
                out.dropped.append(finding)

    # Правила кодом: цитата взята из текста требования, поэтому проверена по построению.
    # Если модель уже нашла проблему той же характеристики у того же требования,
    # находку правила не дублируем.
    covered = {(f.requirement_ids[0], f.characteristic) for f in out.findings}
    for rf in rule_findings:
        key = (rf.requirement_ids[0], rf.characteristic)
        if key in covered or rf.requirement_ids[0] not in by_id:
            continue
        out.findings.append(rf.model_copy(update={"verified": True}))
    return out


def apply_assessments(
    reqs: list[Requirement], judged: CheckEachOutput, findings: list[Finding]
) -> list[Requirement]:
    """Оценка характеристики = мнение модели, но «нарушение» без подтверждённой находки → «неясно»,
    а подтверждённая находка всегда отражается в оценке."""
    by_req: dict[str, dict[Characteristic, Assessment]] = {}
    for f in findings:
        if f.scope != "requirement" or f.characteristic is None:
            continue
        mark = Assessment.VIOLATION if f.verdict == "violation" else Assessment.UNCLEAR
        current = by_req.setdefault(f.requirement_ids[0], {})
        if current.get(f.characteristic) != Assessment.VIOLATION:
            current[f.characteristic] = mark

    out: list[Requirement] = []
    for req in reqs:
        item = judged.items.get(req.report_id)
        assessment: dict[Characteristic, Assessment] = {}
        if item is not None:
            for ch in Characteristic:
                value = Assessment(getattr(item.assessment, ch.value))
                if value == Assessment.VIOLATION:
                    value = Assessment.UNCLEAR
                assessment[ch] = value
        for ch, mark in by_req.get(req.report_id, {}).items():
            assessment[ch] = mark
        out.append(req.model_copy(update={"assessment": assessment}))
    return out


def set_findings(
    raw: list[SetFinding],
    reqs: list[Requirement],
    doc: Document,
    thresholds: Thresholds,
    criteria: Criteria,
) -> VerifyOutput:
    blocks = candidate_blocks(doc)
    threshold = thresholds.quote_min_similarity
    by_id = {r.report_id: r for r in reqs}
    out = VerifyOutput()

    for sf in raw:
        req_ids = [rid for rid in dict.fromkeys(sf.requirement_ids) if rid in by_id]
        prefer: list[Block] = []
        for rid in req_ids:
            prefer.extend(_block_of(by_id[rid], blocks))
        placed: list[tuple[str, Block]] = []
        for quote in sf.quotes:
            block = locate(quote, blocks, threshold, prefer=prefer)
            if block is not None and all(b.block_id != block.block_id for _, b in placed):
                placed.append((quote.strip(), block))
        category = Category(sf.category)
        kinds = [by_id[rid].kind for rid in req_ids]
        finding = Finding(
            finding_id="F-000",
            scope="set",
            requirement_ids=req_ids,
            category=category,
            severity=final_severity(category, sf.severity, kinds, thresholds),
            verdict="violation",
            quote=(placed[0][0] if placed else (sf.quotes[0] if sf.quotes else "")),
            anchor=placed[0][1].anchor if placed else doc.blocks[0].anchor,
            related_anchors=[b.anchor for _, b in placed[1:]],
            related_quotes=[q for q, _ in placed[1:]],
            explanation=sf.explanation.strip(),
            suggestion=(sf.suggestion or "").strip() or None,
            source="llm",
            standard_ref="ISO/IEC/IEEE 29148:2018 5.2.6; IEEE 830 4.3",
        )
        need = 2 if category in (Category.CONTRADICTION, Category.DUPLICATE) else 1
        if len(placed) >= need:
            out.findings.append(finding.model_copy(update={"verified": True}))
        else:
            out.dropped.append(finding)
    return out


def number(findings: list[Finding], reqs: list[Requirement]) -> list[Finding]:
    """F-ID по порядку: находки требований в порядке требований, затем находки набора."""
    order = {r.report_id: n for n, r in enumerate(reqs)}

    def key(f: Finding) -> tuple:
        first = min((order.get(rid, 10**6) for rid in f.requirement_ids), default=10**6)
        return (f.scope != "requirement", first, SEVERITY_RANK[f.severity])

    ordered = sorted(findings, key=key)
    return [f.model_copy(update={"finding_id": f"F-{i:03d}"}) for i, f in enumerate(ordered, 1)]


def suppress_noise(
    findings: list[Finding], rules: FindingsFilter
) -> tuple[list[Finding], list[Finding]]:
    """Убирает шум из находок требований: minor-«неясно» и всё сверх лимита на требование.
    Находки набора не трогаем. Возвращает (оставленные, убранные)."""
    kept: list[Finding] = []
    suppressed: list[Finding] = []
    per_req: dict[str, list[Finding]] = {}
    for f in findings:
        if f.scope != "requirement":
            kept.append(f)
        elif rules.drop_unclear_minor and f.verdict == "unclear" and f.severity == Severity.MINOR:
            suppressed.append(f)
        else:
            per_req.setdefault(f.requirement_ids[0], []).append(f)
    for group in per_req.values():
        group.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.verdict != "violation"))
        kept.extend(group[: rules.max_per_requirement])
        suppressed.extend(group[rules.max_per_requirement :])
    return kept, suppressed
