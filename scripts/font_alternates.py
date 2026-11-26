"""Таблица «глиф-альтернатива → Unicode» для шрифтов PDF-отчёта (dev-скрипт, нужен fontTools).

Почему: MuPDF при вёрстке (HarfBuzz) применяет GSUB-подстановки шрифта, например `locl` у Golos
Text на латинице по умолчанию заменяет «A» на глиф «A.1». У альтернативы нет записи в cmap,
поэтому в ToUnicode PDF её нет, и при извлечении текста MuPDF подставляет номер глифа как
Unicode: 0x0102 → «Ă» (ADR-010). Таблица отсюда дописывается в ToUnicode после рендера
(reqcheck/render/tounicode.py), сами шрифты не меняются.

Запуск:  uv run python scripts/font_alternates.py
"""

import json
from pathlib import Path

from fontTools.ttLib import TTFont

FONTS = Path(__file__).resolve().parents[1] / "reqcheck" / "render" / "fonts"
OUT = FONTS / "alternates.json"


def subtables(lookup):
    for st in lookup.SubTable:
        if st.__class__.__name__ == "ExtensionSubst":
            yield st.ExtSubTable.LookupType, st.ExtSubTable
        else:
            yield lookup.LookupType, st


def alternates(path: Path) -> dict[str, str]:
    font = TTFont(str(path))
    order = font.getGlyphOrder()
    gid = {name: i for i, name in enumerate(order)}
    text: dict[str, str] = {}
    for code, name in font.getBestCmap().items():
        text.setdefault(name, chr(code))
    found: dict[str, str] = {}
    if "GSUB" not in font:
        return {}
    # Несколько проходов: альтернатива альтернативы (A → A.1 → A.2) тоже получает текст.
    for _ in range(3):
        for lookup in font["GSUB"].table.LookupList.Lookup:
            for kind, st in subtables(lookup):
                if kind == 1:
                    pairs = [([src], dst) for src, dst in st.mapping.items()]
                elif kind == 3:
                    pairs = [([src], d) for src, dsts in st.alternates.items() for d in dsts]
                elif kind == 4:
                    pairs = [
                        ([first, *lig.Component], lig.LigGlyph)
                        for first, ligs in st.ligatures.items()
                        for lig in ligs
                    ]
                else:
                    continue
                for srcs, dst in pairs:
                    if dst in text or not all(s in text for s in srcs):
                        continue
                    text[dst] = "".join(text[s] for s in srcs)
                    found[str(gid[dst])] = text[dst]
    return dict(sorted(found.items(), key=lambda kv: int(kv[0])))


def main() -> None:
    table = {p.stem: alternates(p) for p in sorted(FONTS.glob("*.ttf"))}
    OUT.write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for name, items in table.items():
        print(f"{name}: {len(items)} альтернатив")


if __name__ == "__main__":
    main()
