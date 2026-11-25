"""Разобранный документ: блоки текста с якорями (результат этапа ingest)."""

from typing import Literal

from pydantic import Field

from reqcheck.formats import InputFormat
from reqcheck.schemas.common import Anchor, Strict

BlockKind = Literal["heading", "paragraph", "list_item", "table_row"]


class Block(Strict):
    block_id: str = Field(pattern=r"^B-\d{4,}$")
    kind: BlockKind
    text: str
    anchor: Anchor


class Document(Strict):
    source_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    format: InputFormat
    pages: int | None = Field(default=None, ge=1)
    blocks: list[Block] = Field(default_factory=list)
