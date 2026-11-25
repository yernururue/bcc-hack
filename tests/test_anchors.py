"""Разбор документа: блоки и якоря для MD/TXT и PDF (этап ingest). Сеть не нужна."""

from pathlib import Path

import pytest

from reqcheck.config import IngestConfig, ingest_config
from reqcheck.errors import InputError, NoTextLayerError
from reqcheck.formats import detect_input
from reqcheck.ingest import ingest
from reqcheck.ingest.normalize import normalize
from reqcheck.schemas.document import Document

MD = """\
# Название документа

Вступление без раздела.

## Часть A

### Функциональные требования
* **Ф-1.** Порог от 1 000 до 1 000 000 ₸.
  Продолжение пункта на второй строке.
  * Уточнение без своего номера.
* **Ф-2.** Месяц по Алматы.

### Логика
1. Проверить сессию.
2. Проверить карту.

| Parameter | Format |
| :--- | :--- |
| `cardId` | string |
| Длинное описание с пробелами | int |

**200.** Успех.
**400.** Ошибка запроса.

```json
{"result": "OK"}
```

FR-7: Requirement with a plain id.
4.2.1 Нумерация раздела.
"""


def write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    p = tmp_path / name
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


def parse_md(tmp_path: Path, text: str = MD, name: str = "req.md") -> Document:
    return ingest(detect_input(write(tmp_path, name, text)))


def find(doc: Document, prefix: str):
    return next(b for b in doc.blocks if b.text.startswith(prefix))


def test_requirement_item_and_section_path(tmp_path: Path) -> None:
    b = find(parse_md(tmp_path), "Ф-2.")
    assert b.kind == "list_item"
    assert b.anchor.section_path == ["Часть A", "Функциональные требования"]
    assert b.anchor.item == "Ф-2"
    assert b.anchor.line == 11
    assert b.anchor.label() == "Часть A › Функциональные требования › Ф-2 (строка 11)"


def test_single_h1_is_title_not_section(tmp_path: Path) -> None:
    doc = parse_md(tmp_path)
    assert doc.blocks[0].kind == "heading"
    assert find(doc, "Вступление").anchor.section_path == []


def test_two_h1_are_sections(tmp_path: Path) -> None:
    doc = parse_md(tmp_path, "# Часть A\n\nтекст a\n\n# Часть B\n\nтекст b\n")
    assert find(doc, "текст b").anchor.section_path == ["Часть B"]


def test_list_continuation_and_nested_inherit_item(tmp_path: Path) -> None:
    doc = parse_md(tmp_path)
    f1 = find(doc, "Ф-1.")
    assert "Продолжение пункта" in f1.text
    assert "**" not in f1.text
    nested = find(doc, "Уточнение")
    assert nested.anchor.item == "Ф-1"


def test_ordered_list_gets_point_label(tmp_path: Path) -> None:
    b = find(parse_md(tmp_path), "Проверить карту")
    assert b.anchor.item == "п. 2"
    assert b.anchor.section_path[-1] == "Логика"


def test_table_rows(tmp_path: Path) -> None:
    doc = parse_md(tmp_path)
    header = find(doc, "Parameter")
    assert header.kind == "table_row" and header.anchor.item is None
    row = find(doc, "cardId")
    assert row.text == "cardId | string"
    assert row.anchor.item == "cardId"
    assert find(doc, "Длинное описание").anchor.item is None
    assert not any(b.text.startswith(":---") for b in doc.blocks)


def test_bold_ids_on_adjacent_lines_split(tmp_path: Path) -> None:
    doc = parse_md(tmp_path)
    assert find(doc, "200.").anchor.item == "200"
    assert find(doc, "400.").anchor.item == "400"


def test_code_fence_kept_verbatim(tmp_path: Path) -> None:
    b = find(parse_md(tmp_path), '{"result"')
    assert b.kind == "paragraph"
    assert b.text == '{"result": "OK"}'


def test_plain_ids(tmp_path: Path) -> None:
    doc = parse_md(tmp_path)
    assert find(doc, "FR-7").anchor.item == "FR-7"
    assert find(doc, "4.2.1").anchor.item == "4.2.1"


def test_block_ids_sequential_and_hash(tmp_path: Path) -> None:
    doc = parse_md(tmp_path)
    assert [b.block_id for b in doc.blocks[:3]] == ["B-0001", "B-0002", "B-0003"]
    assert len(doc.sha256) == 64 and doc.format == "md" and doc.pages is None


def test_txt_cp1251(tmp_path: Path) -> None:
    p = write(tmp_path, "req.txt", "Требование 1.1 Система работает.\n".encode("cp1251"))
    doc = ingest(detect_input(p))
    assert doc.format == "txt"
    assert doc.blocks[0].text == "Требование 1.1 Система работает."


def test_whitespace_only_document_rejected(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        parse_md(tmp_path, "\n   \n\t\n")


def test_normalize_keeps_meaning() -> None:
    raw = "тре­бова-\nния № 5 ﬁle 1 000"
    assert normalize(raw) == "требования № 5 file 1 000"


def test_config_requires_id_group() -> None:
    with pytest.raises(ValueError):
        IngestConfig(item_patterns=[r"^\d+"])


def test_repo_config_loads() -> None:
    assert ingest_config().compiled_item_patterns()


def _make_pdf(path: Path, html: str) -> None:
    import pymupdf

    story = pymupdf.Story(html=html)
    writer = pymupdf.DocumentWriter(str(path))
    page = pymupdf.paper_rect("a4")
    more = True
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(page + (50, 50, -50, -50))
        story.draw(dev)
        writer.end_page()
    writer.close()


def test_pdf_pages_sections_and_items(tmp_path: Path) -> None:
    filler = "<p>Заполнитель страницы.</p>" * 60
    html = (
        "<h1>Спецификация</h1><h2>1. Требования</h2>"
        "<ul><li><b>Ф-1.</b> Порог до 1 000 000 ₸.</li>"
        "<li><b>Ф-2.</b> Қазақша: әғқңөұүһі.</li></ul>"
        f"{filler}<h2>2. Данные</h2><p><b>Д-1.</b> Сумма в тиынах.</p>"
    )
    p = tmp_path / "spec.pdf"
    _make_pdf(p, html)
    doc = ingest(detect_input(p))
    assert doc.format == "pdf" and doc.pages is not None and doc.pages >= 2

    f2 = find(doc, "Ф-2.")
    assert f2.anchor.page == 1
    assert f2.anchor.item == "Ф-2"
    assert f2.anchor.line is None
    assert "әғқңөұүһі" in f2.text
    assert f2.anchor.label() == "стр. 1 › 1. Требования › Ф-2"

    d1 = find(doc, "Д-1.")
    assert d1.anchor.page == doc.pages
    assert d1.anchor.section_path == ["2. Данные"]


def test_pdf_without_text_layer(tmp_path: Path) -> None:
    import pymupdf

    p = tmp_path / "scan.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(p)
    with pytest.raises(NoTextLayerError):
        ingest(detect_input(p))
