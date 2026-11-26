"""Report → HTML → PDF (pymupdf.Story). Шрифты Golos Text и JetBrains Mono встроены (OFL).

TTF собраны из woff2-подмножеств web/fonts: FreeType в PyMuPDF не читает woff2 (проверено).
"""

from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from reqcheck.config import Criteria, criteria
from reqcheck.render import context
from reqcheck.schemas import Report

FONTS_DIR = Path(__file__).parent / "fonts"
A4 = (595.0, 842.0)
MARGIN = 51.0  # 18 мм
FOOTER = 22.0

# Токены docs/05-design.md: синий — только ссылки, красный — только blocker и «нельзя».
CSS = """
@font-face { font-family: Golos; src: url(GolosText-Regular.ttf); }
@font-face { font-family: Golos; font-weight: bold; src: url(GolosText-SemiBold.ttf); }
@font-face { font-family: Mono; src: url(JetBrainsMono-Regular.ttf); }
body { font-family: Golos; font-size: 10.5pt; line-height: 1.4; color: #0A0A0A; }
h1 { font-size: 20pt; font-weight: bold; margin: 2pt 0 8pt 0; }
h2 { font-size: 14pt; font-weight: bold; margin: 20pt 0 8pt 0; padding-bottom: 4pt;
     border-bottom: 0.75pt solid #0A0A0A; }
h3 { font-size: 11pt; font-weight: bold; margin: 14pt 0 4pt 0; }
p { margin: 0 0 4pt 0; }
code { font-family: Mono; font-size: 9pt; }
code.small { font-size: 8pt; }
a { color: #0052FF; text-decoration: none; }
b { font-weight: bold; }
.kicker { color: #6B6B6B; font-size: 9pt; margin: 0; }
.lead { font-size: 11pt; margin: 4pt 0 6pt 0; }
.warn { color: #0A0A0A; border-left: 2pt solid #0A0A0A; padding-left: 6pt; }
.muted { color: #6B6B6B; }
.where { color: #6B6B6B; font-weight: normal; font-size: 9pt; }
.no { color: #C62828; }
.sev { color: #0A0A0A; }
.sev-blocker { color: #C62828; font-weight: bold; }
.quote { margin: 0 0 6pt 0; padding-left: 8pt; border-left: 1.5pt solid #C8C8C8; }
.quote-line { margin: 0 0 3pt 0; }
.cite { color: #0A0A0A; padding-left: 8pt; border-left: 1.5pt solid #C8C8C8; margin: 2pt 0 2pt 0; }
.fix { color: #0A0A0A; }
.finding { margin: 6pt 0 8pt 0; }
.question { margin: 0 0 10pt 0; }
.fh { margin: 0 0 2pt 0; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 8pt 0; }
th { text-align: left; font-weight: bold; font-size: 8.5pt; color: #6B6B6B;
     border-bottom: 0.75pt solid #C8C8C8; padding: 3pt 4pt 3pt 0; }
td { vertical-align: top; padding: 3pt 4pt 3pt 0; border-bottom: 0.5pt solid #E6E6E6; }
table.grid td { font-size: 9pt; }
table.kv td.k { color: #6B6B6B; padding-right: 12pt; }
table.marks th { font-size: 7.5pt; text-align: center; padding: 2pt 1pt; width: 52pt; }
table.marks td { text-align: center; font-size: 10pt; padding: 2pt 1pt; width: 52pt; }
table.marks td.bad { color: #C62828; }
ol { margin: 0 0 6pt 0; padding-left: 16pt; }
li { margin: 0 0 3pt 0; }
"""

FOOTER_CSS = """
@font-face { font-family: Golos; src: url(GolosText-Regular.ttf); }
p { font-family: Golos; font-size: 8pt; color: #6B6B6B; margin: 0; }
"""

_env = Environment(
    loader=PackageLoader("reqcheck.render", "templates"),
    autoescape=select_autoescape(["html", "j2"], default=True),
    undefined=StrictUndefined,
)


def render_html(report: Report, crit: Criteria | None = None) -> str:
    return _env.get_template("report.html.j2").render(**context.build(report, crit or criteria()))


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_pdf(report: Report, path: Path, crit: Criteria | None = None) -> None:
    import pymupdf  # тяжёлый импорт только для PDF

    archive = pymupdf.Archive(str(FONTS_DIR))
    story = pymupdf.Story(html=render_html(report, crit), user_css=CSS, archive=archive)
    page = pymupdf.Rect(0, 0, *A4)
    body = pymupdf.Rect(MARGIN, MARGIN, A4[0] - MARGIN, A4[1] - MARGIN - FOOTER)

    def rectfn(_rect_num: int, _filled):
        return page, body, None

    doc = story.write_with_links(rectfn)
    total = doc.page_count
    name = _escape(report.run.document.name)
    version = _escape(report.run.agent_version)
    for page_no, pg in enumerate(doc, start=1):
        box = pymupdf.Rect(MARGIN, A4[1] - MARGIN - 10, A4[0] - MARGIN, A4[1] - MARGIN + 12)
        pg.insert_htmlbox(
            box,
            f"<p>{name} · ReqCheck {version} · стр. {page_no} из {total}</p>",
            css=FOOTER_CSS,
            archive=archive,
        )
    doc.set_metadata(
        {
            "title": f"Отчёт о проверке требований: {report.run.document.name}",
            "author": "ReqCheck",
            "creator": f"ReqCheck {report.run.agent_version}",
            "producer": "PyMuPDF",
        }
    )
    # Колонтитул встраивает шрифт на каждой странице: подмножества + garbage=4 (дедуп потоков)
    # дают ~240 КБ вместо ~1,7 МБ. Подмножество строит сам MuPDF, fontTools не нужен.
    doc.subset_fonts()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
