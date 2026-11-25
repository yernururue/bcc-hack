"""Этап extract: блоки документа → требования с R-ID, дословной цитатой и якорем.

LLM решает, что является требованием, и делит составные; цитата и место проверяются кодом.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from reqcheck import prompts
from reqcheck.llm.base import LLMClient
from reqcheck.schemas import Requirement, RequirementKind
from reqcheck.schemas.document import Block, Document
from reqcheck.stages.quotes import locate, match_key, similarity

BLOCKS_PER_CALL = 150


class ExtractedRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    doc_id: str | None
    kind: RequirementKind
    summary: str
    quote: str


class ExtractResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[ExtractedRequirement]


@dataclass
class ExtractOutput:
    requirements: list[Requirement]
    split_composites: int = 0
    unplaced: list[str] = field(default_factory=list)  # что модель назвала, но не нашлось в тексте


def candidate_blocks(doc: Document) -> list[Block]:
    """Заголовки — контекст (они уже в якоре), сами требованиями не бывают."""
    return [b for b in doc.blocks if b.kind != "heading"]


def format_block(block: Block) -> str:
    text = block.text.replace("\n", " ⏎ ")
    return f"{block.block_id} · {block.anchor.label()} · {block.kind} · {text}"


def _doc_id(item: ExtractedRequirement, block: Block) -> str | None:
    """Номер из ответа модели принимаем, только если он есть в тексте блока или в якоре."""
    if item.doc_id:
        candidate = item.doc_id.strip()
        if candidate == block.anchor.item or match_key(candidate) in match_key(block.text):
            return candidate
    item_id = block.anchor.item
    if item_id and not item_id.startswith(("п. ", "п.")):
        return item_id
    return None


def build(doc: Document, items: list[ExtractedRequirement], threshold: float) -> ExtractOutput:
    blocks = candidate_blocks(doc)
    by_id = {b.block_id: b for b in blocks}
    order = {b.block_id: n for n, b in enumerate(blocks)}
    placed: list[tuple[int, int, Block, ExtractedRequirement, str]] = []
    unplaced: list[str] = []
    seen: set[tuple[str, str]] = set()

    for n, item in enumerate(items):
        block = by_id.get(item.block_id)
        if block is None or similarity(item.quote, block.text) < threshold:
            # Модель ошиблась блоком или пересказала цитату: ищем текст по всему документу.
            found = locate(item.quote, blocks, threshold)
            if found is not None:
                block = found
            elif block is None:
                unplaced.append(item.quote)
                continue
        quote = item.quote if similarity(item.quote, block.text) >= threshold else block.text
        key = (block.block_id, match_key(quote))
        if key in seen:
            continue
        seen.add(key)
        placed.append((order[block.block_id], n, block, item, quote))

    placed.sort(key=lambda p: (p[0], p[1]))
    out: list[Requirement] = []
    first_in_block: dict[str, str] = {}
    split_blocks: set[str] = set()
    for i, (_, _, block, item, quote) in enumerate(placed, start=1):
        rid = f"R-{i:03d}"
        doc_id = _doc_id(item, block)
        anchor = block.anchor
        if anchor.item is None and doc_id:
            anchor = anchor.model_copy(update={"item": doc_id})
        split_from = first_in_block.get(block.block_id)
        if split_from:
            split_blocks.add(block.block_id)
        else:
            first_in_block[block.block_id] = rid
        out.append(
            Requirement(
                report_id=rid,
                doc_id=doc_id,
                kind=item.kind,
                summary=item.summary.strip(),
                quote=quote.strip(),
                anchor=anchor,
                split_from=split_from,
            )
        )
    return ExtractOutput(out, len(split_blocks), unplaced)


def run(doc: Document, llm: LLMClient, threshold: float) -> ExtractOutput:
    blocks = candidate_blocks(doc)
    system = prompts.load("system")
    items: list[ExtractedRequirement] = []
    for start in range(0, len(blocks), BLOCKS_PER_CALL):
        chunk = blocks[start : start + BLOCKS_PER_CALL]
        user = prompts.render("extract", blocks="\n".join(format_block(b) for b in chunk))
        result = llm.complete(system=system, user=user, schema=ExtractResult, stage="extract")
        items.extend(result.value.requirements)
    return build(doc, items, threshold)
