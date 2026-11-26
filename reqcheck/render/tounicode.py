"""Дописывает в ToUnicode шрифтов PDF глифы-альтернативы из GSUB (ADR-010).

MuPDF строит ToUnicode по cmap шрифта, а вёрстка (HarfBuzz) ставит глифы после GSUB-подстановок:
у Golos Text `locl` на латинице заменяет «A» на «A.1», у которого нет cmap. Без записи в ToUnicode
извлечение текста возвращает номер глифа как символ: 0x0102 → «Ă». Таблица альтернатив
построена заранее (scripts/font_alternates.py) и лежит рядом со шрифтами; fontTools не нужен.
"""

import json
import re
from functools import cache
from typing import Any

from reqcheck.render.pdf import FONTS_DIR

_RANGE = re.compile(rb"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*(?:<[0-9a-fA-F]+>|\[)")
_CHAR = re.compile(rb"<([0-9a-fA-F]{4})>\s*<([0-9a-fA-F]+)>")
_SECTION = re.compile(rb"begin(bfchar|bfrange)(.*?)end\1", re.S)


def _key(name: str) -> str:
    """«XGPUPH+Golos Text Regular» и «GolosText-Regular» → «golostextregular»."""
    return re.sub(r"[^a-z0-9]", "", name.split("+", 1)[-1].lower())


@cache
def _tables() -> dict[str, dict[int, str]]:
    raw = json.loads((FONTS_DIR / "alternates.json").read_text(encoding="utf-8"))
    return {_key(name): {int(g): t for g, t in items.items()} for name, items in raw.items()}


def _mapped(cmap: bytes) -> set[int]:
    out: set[int] = set()
    for kind, body in _SECTION.findall(cmap):
        if kind == b"bfrange":
            for lo, hi in _RANGE.findall(body):
                out.update(range(int(lo, 16), int(hi, 16) + 1))
        else:
            out.update(int(cid, 16) for cid, _ in _CHAR.findall(body))
    return out


def _utf16(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


def patch_cmap(cmap: bytes, alternates: dict[int, str]) -> bytes:
    """Новый ToUnicode: прежние записи плюс альтернативы, которых в нём нет."""
    missing = sorted(set(alternates) - _mapped(cmap))
    if not missing or b"endcmap" not in cmap:
        return cmap
    parts = []
    for i in range(0, len(missing), 100):  # не больше 100 записей в секции (спецификация CMap)
        chunk = missing[i : i + 100]
        lines = "\n".join(f"<{cid:04X}> <{_utf16(alternates[cid])}>" for cid in chunk)
        parts.append(f"{len(chunk)} beginbfchar\n{lines}\nendbfchar\n")
    head, tail = cmap.rsplit(b"endcmap", 1)
    return head + "".join(parts).encode() + b"endcmap" + tail


def fix_tounicode(doc: Any) -> int:
    """Чинит ToUnicode у всех шрифтов Type0 документа pymupdf; возвращает число исправленных."""
    tables = _tables()
    fixed = 0
    for xref in range(1, doc.xref_length()):
        if doc.xref_get_key(xref, "Subtype") != ("name", "/Type0"):
            continue
        kind, base = doc.xref_get_key(xref, "BaseFont")
        alternates = tables.get(_key(base.lstrip("/").replace("#20", " ")))
        tu_kind, tu = doc.xref_get_key(xref, "ToUnicode")
        if kind != "name" or not alternates or tu_kind != "xref":
            continue
        tu_xref = int(tu.split()[0])
        cmap = doc.xref_stream(tu_xref)
        new = patch_cmap(cmap, alternates)
        if new != cmap:
            doc.update_stream(tu_xref, new)
            fixed += 1
    return fixed
