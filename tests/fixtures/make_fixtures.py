"""Генератор PDF-фикстур для eval и тестов ингеста (docs/02-trd.md, §10).

Запуск:  uv run python tests/fixtures/make_fixtures.py

Текстовый слой чинится так же, как у PDF-отчёта: альтернативы глифов GSUB дописываются в
ToUnicode (reqcheck/render/tounicode.py, ADR-010), поэтому латинская «A» извлекается как «A».
Пишет defects.pdf, contradictions.pdf, clean.pdf рядом со скриптом (готовые PDF коммитятся), а
также PDF-версии документов корпуса из src/*.md (подмножество Markdown: заголовки, абзацы, списки с
номером пункта, таблицы). Markdown-документы корпуса лежат рядом как есть.
Тексты общие, про вымышленный интернет-магазин: ничего от документа организаторов.
Шрифты те же, что у PDF-отчёта (reqcheck/render/fonts, OFL). Ожидаемые находки — в expected.yaml;
при генерации проверяем, что каждая цитата-якорь есть в тексте документа дословно.
"""

import re
import sys
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

import pymupdf
import yaml

from reqcheck.render.pdf import A4, FONTS_DIR, MARGIN
from reqcheck.render.tounicode import fix_tounicode
from reqcheck.stages.quotes import match_key

HERE = Path(__file__).parent
SRC = HERE / "src"

CSS = """
@font-face { font-family: Golos; src: url(GolosText-Regular.ttf); }
@font-face { font-family: Golos; font-weight: bold; src: url(GolosText-SemiBold.ttf); }
body { font-family: Golos; font-size: 11pt; line-height: 1.4; color: #0A0A0A; }
h1 { font-size: 20pt; font-weight: bold; margin: 0 0 8pt 0; }
h2 { font-size: 14pt; font-weight: bold; margin: 16pt 0 6pt 0; }
h3 { font-size: 12pt; font-weight: bold; margin: 12pt 0 4pt 0; }
p { margin: 0 0 6pt 0; }
li { margin: 0 0 5pt 0; }
table { border-collapse: collapse; margin: 4pt 0 8pt 0; }
th { text-align: left; font-weight: bold; border: 0.75pt solid #0A0A0A; padding: 3pt 5pt; }
td { vertical-align: top; border: 0.75pt solid #0A0A0A; padding: 3pt 5pt; }
"""


@dataclass
class Section:
    heading: str
    intro: str = ""
    items: list[tuple[str, str]] = field(default_factory=list)  # (номер, текст)
    table: list[list[str]] = field(default_factory=list)  # первая строка — заголовок


@dataclass
class Doc:
    file: str
    title: str
    lead: str
    sections: list[Section]

    def html(self) -> str:
        out = [f"<h1>{escape(self.title)}</h1>", f"<p>{escape(self.lead)}</p>"]
        for s in self.sections:
            out.append(f"<h2>{escape(s.heading)}</h2>")
            if s.intro:
                out.append(f"<p>{escape(s.intro)}</p>")
            if s.items:
                out.append("<ul>")
                for num, text in s.items:
                    label = f"<b>{escape(num)}</b> " if num else ""
                    out.append(f"<li>{label}{escape(text)}</li>")
                out.append("</ul>")
            if s.table:
                head, *rows = s.table
                out.append("<table><tr>" + "".join(f"<th>{escape(c)}</th>" for c in head) + "</tr>")
                for row in rows:
                    out.append("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>")
                out.append("</table>")
        return "\n".join(out)

    def text(self) -> str:
        parts = [self.title, self.lead]
        for s in self.sections:
            parts += [s.heading, s.intro]
            parts += [f"{n} {t}" for n, t in s.items]
            parts += [c for row in s.table for c in row]
        return "\n".join(parts)


DEFECTS = Doc(
    file="defects.pdf",
    title="Личный кабинет покупателя",
    lead=(
        "Учебная спецификация. Версия 0.3. Документ описывает кабинет покупателя "
        "и программу бонусов."
    ),
    sections=[
        Section(
            "1. Функциональные требования",
            items=[
                (
                    "Ф-1.",
                    "Система должна позволять покупателю зарегистрироваться по номеру мобильного "
                    "телефона в формате +7XXXXXXXXXX и подтвердить его одноразовым кодом из "
                    "четырёх цифр, действующим 5 минут.",
                ),
                (
                    "Ф-2.",
                    "Система должна быстро формировать выписку по заказам за выбранный период.",
                ),
                (
                    "Ф-3.",
                    "При регистрации система должна отправлять SMS с кодом подтверждения на "
                    "указанный номер телефона.",
                ),
                (
                    "Ф-4.",
                    "Система должна применять к заказу скидку в зависимости от уровня покупателя.",
                ),
                (
                    "Ф-5.",
                    "Система должна регистрировать возврат товара, пересчитывать бонусные баллы, "
                    "отправлять покупателю чек возврата и блокировать аккаунты с подозрительной "
                    "активностью.",
                ),
                (
                    "Ф-6.",
                    "Покупатель может отменить заказ в статусе «Новый» до передачи его в сборку; "
                    "после отмены система возвращает оплаченную сумму на тот же способ оплаты в "
                    "течение 3 рабочих дней.",
                ),
                (
                    "Ф-7.",
                    "Система должна воспроизводить звуковой сигнал фанфар при каждом успешном "
                    "входе клиента в личный кабинет.",
                ),
                (
                    "",
                    "Ещё хорошо бы, чтобы можно было как-нибудь смотреть историю своих покупок.",
                ),
                (
                    "Ф-8.",
                    "Система должна отправлять на введённый при регистрации номер телефона SMS "
                    "с кодом подтверждения.",
                ),
            ],
        ),
        Section(
            "2. Нефункциональные требования",
            items=[
                (
                    "НФТ-1.",
                    "Система должна обеспечивать высокий уровень безопасности персональных данных "
                    "покупателей.",
                ),
                (
                    "НФТ-2.",
                    "Все данные заказов должны храниться в базе данных PostgreSQL 16 в таблице "
                    "orders с индексом по полю created_at.",
                ),
                (
                    "НФТ-3.",
                    "Система должна работать без перерывов и не допускать потери ни одного заказа "
                    "ни при каких сбоях.",
                ),
                (
                    "НФТ-4.",
                    "Страница каталога должна открываться не дольше 2 секунд при нагрузке до "
                    "500 одновременных пользователей, измеряется на 95-м процентиле.",
                ),
            ],
        ),
        Section(
            "3. Данные",
            items=[
                (
                    "Д-1.",
                    "Бонусные баллы начисляются в размере 5 % от суммы заказа: за заказ на "
                    "2 000 ₸ покупатель получает 200 баллов.",
                ),
                (
                    "Д-2.",
                    "Номер телефона хранится в формате E.164 и не может быть привязан более чем "
                    "к одному аккаунту.",
                ),
            ],
        ),
    ],
)

CONTRADICTIONS = Doc(
    file="contradictions.pdf",
    title="Заказы и доставка: спецификация в двух частях",
    lead=(
        "Учебный комплект из двух частей. Часть Б — описание интерфейса обмена, "
        "на которое ссылается часть А."
    ),
    sections=[
        Section(
            "Часть А. Требования",
            items=[
                (
                    "Ф-1.",
                    "Покупатель может сохранить в профиле не более 5 адресов доставки.",
                ),
                (
                    "Ф-2.",
                    "Покупатель может отменить заказ без комиссии, если с момента оформления "
                    "прошло не более 30 минут.",
                ),
                (
                    "Ф-3.",
                    "После оформления заказа система присваивает ему номер и сохраняет заказ со "
                    "статусом «Новый».",
                ),
                (
                    "Ф-4.",
                    "При успешном создании заказа сервис заказов возвращает ответ с кодом 200.",
                ),
                (
                    "Ф-5.",
                    "Система должна хранить данные выполненных заказов 3 года и затем удалять их.",
                ),
                (
                    "Ф-6.",
                    "Система должна отправлять покупателю SMS при передаче заказа курьеру.",
                ),
            ],
        ),
        Section(
            "Часть Б. Интерфейс обмена и данные",
            items=[
                (
                    "АПИ-1.",
                    "Метод POST /orders при успешном создании заказа возвращает код 201 и "
                    "идентификатор заказа.",
                ),
                (
                    "АПИ-2.",
                    "Метод GET /orders/{id} возвращает заказ, а при отсутствии заказа — код 404.",
                ),
                (
                    "Д-1.",
                    "Максимальное число адресов доставки в профиле покупателя равно 10.",
                ),
                (
                    "Д-2.",
                    "Данные заказов удаляются из хранилища через 12 месяцев после выполнения "
                    "заказа.",
                ),
                (
                    "Д-3.",
                    "Номер заказа — целое число от 1 до 999 999 999.",
                ),
            ],
        ),
        Section(
            "Критерии приёмки",
            table=[
                ["Условие", "Ожидаемый результат"],
                [
                    "Отмена заказа через 45 минут после оформления",
                    "Заказ отменён без комиссии",
                ],
                [
                    "Запрос GET /orders/{id} несуществующего заказа",
                    "Ответ с кодом 404",
                ],
            ],
        ),
    ],
)

CLEAN = Doc(
    file="clean.pdf",
    title="Корзина покупателя",
    lead="Учебная спецификация. Версия 1.0. Документ описывает работу корзины покупателя.",
    sections=[
        Section(
            "1. Функциональные требования",
            items=[
                (
                    "Ф-1.",
                    "Система должна добавлять товар в корзину по нажатию кнопки «В корзину» при "
                    "наличии товара на складе и показывать в корзине название, цену и количество "
                    "добавленного товара.",
                ),
                (
                    "Ф-2.",
                    "Если запрошенное количество товара превышает остаток на складе, система "
                    "должна не добавлять товар в корзину и показывать сообщение «Доступно "
                    "N шт.», где N — остаток на складе.",
                ),
                (
                    "Ф-3.",
                    "Система должна позволять покупателю изменить количество товара в корзине "
                    "целым числом от 1 до 99.",
                ),
                (
                    "Ф-4.",
                    "Система должна удалять товар из корзины при нажатии кнопки «Удалить» "
                    "рядом с товаром без дополнительного подтверждения.",
                ),
                (
                    "Ф-5.",
                    "Система должна пересчитывать итоговую сумму корзины как сумму произведений "
                    "цены и количества по всем товарам после каждого изменения корзины.",
                ),
                (
                    "Ф-6.",
                    "Система должна сохранять корзину авторизованного покупателя на 30 суток с "
                    "момента последнего изменения и удалять её после этого срока.",
                ),
            ],
        ),
        Section(
            "2. Нефункциональные требования",
            items=[
                (
                    "НФТ-1.",
                    "Система должна обновлять страницу корзины после изменения количества "
                    "товара не дольше чем за 1 секунду при нагрузке до 300 одновременных "
                    "пользователей, измеряется на 95-м процентиле.",
                ),
                (
                    "НФТ-2.",
                    "Система должна передавать данные корзины только по защищённому соединению "
                    "TLS версии 1.2 или выше.",
                ),
            ],
        ),
        Section(
            "3. Критерии приёмки",
            table=[
                ["Условие", "Ожидаемый результат"],
                [
                    "На складе 3 шт. товара, покупатель добавляет 5 шт.",
                    "Товар не добавлен, показано сообщение «Доступно 3 шт.»",
                ],
                [
                    "В корзине 2 шт. товара по цене 1 500 ₸",
                    "Итоговая сумма корзины равна 3 000 ₸",
                ],
                [
                    "Корзина не изменялась 31 сутки",
                    "Корзина удалена",
                ],
            ],
        ),
    ],
)

DOCS = [DEFECTS, CONTRADICTIONS, CLEAN]


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    return _BOLD.sub(r"<b>\1</b>", escape(text))


def md_to_html(md: str) -> str:
    """Подмножество Markdown корпуса → HTML для pymupdf.Story."""
    out: list[str] = []
    in_list = False
    table: list[list[str]] = []

    def flush() -> None:
        nonlocal in_list, table
        if in_list:
            out.append("</ul>")
            in_list = False
        if table:
            head, *rows = table
            out.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>")
            for row in rows:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
            out.append("</table>")
            table = []

    for line in md.splitlines():
        line = line.rstrip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not all(set(c) <= set(":- ") for c in cells):
                table.append(cells)
            continue
        if line.startswith("* "):
            if table:
                flush()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line[2:])}</li>")
            continue
        flush()
        if not line:
            continue
        level = len(line) - len(line.lstrip("#"))
        if level:
            out.append(f"<h{level}>{_inline(line[level:].strip())}</h{level}>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    flush()
    return "\n".join(out)


def render(doc: "Doc | str", path: Path, title: str = "") -> None:
    archive = pymupdf.Archive(str(FONTS_DIR))
    html = doc if isinstance(doc, str) else doc.html()
    title = title or (doc.title if not isinstance(doc, str) else path.stem)
    story = pymupdf.Story(html=html, user_css=CSS, archive=archive)
    writer = pymupdf.DocumentWriter(str(path))
    page = pymupdf.Rect(0, 0, *A4)
    body = pymupdf.Rect(MARGIN, MARGIN, A4[0] - MARGIN, A4[1] - MARGIN)

    def rectfn(_n: int, _filled: pymupdf.Rect):
        return page, body, None

    story.write(writer, rectfn)
    writer.close()
    # Метаданные и встроенные подмножества шрифтов: файлы маленькие и воспроизводимые.
    with pymupdf.open(path) as pdf:
        pdf.set_metadata({"title": title, "author": "ReqCheck fixtures"})
        pdf.subset_fonts()
        fix_tounicode(pdf)
        data = pdf.tobytes(garbage=4, deflate=True)
    path.write_bytes(data)


def check_expected() -> None:
    """Каждая цитата-якорь из expected.yaml должна дословно (по ключу сравнения) быть в тексте,
    а раздел режима sections — среди заголовков документа."""
    expected = yaml.safe_load((HERE / "expected.yaml").read_text(encoding="utf-8"))
    texts = {doc.file: doc.text() for doc in DOCS}
    for src in sorted(SRC.glob("*.md")):
        texts[src.stem + ".pdf"] = src.read_text(encoding="utf-8")
    for md in sorted(HERE.glob("*.md")):
        texts[md.name] = md.read_text(encoding="utf-8")
    for name, entry in expected["fixtures"].items():
        if name not in texts:
            raise SystemExit(f"{name}: нет документа")
        text = match_key(texts[name])
        for defect in entry.get("defects") or []:
            for quote in defect.get("quotes", []):
                if match_key(quote) not in text:
                    raise SystemExit(f"{name} {defect['id']}: цитаты нет в тексте: {quote!r}")
            for section in defect.get("sections", []):
                if section != "*" and match_key(section) not in text:
                    raise SystemExit(f"{name} {defect['id']}: нет раздела: {section!r}")


def main() -> None:
    check_expected()
    for doc in DOCS:
        render(doc, HERE / doc.file)
        print(f"{doc.file}: записан")
    for src in sorted(SRC.glob("*.md")):
        md = src.read_text(encoding="utf-8")
        title = md.splitlines()[0].lstrip("# ").strip()
        render(md_to_html(md), HERE / f"{src.stem}.pdf", title)
        print(f"{src.stem}.pdf: записан")


if __name__ == "__main__":
    sys.exit(main())
