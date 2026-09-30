"""Local teaching-draft conversion. No network, model calls, or permanent files."""

import io
import re
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import groupby
from pathlib import Path
from xml.sax.saxutils import escape

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from markdown_it import MarkdownIt
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import LayoutError, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .errors import AppError

MIMES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "md": "text/markdown; charset=utf-8",
}
INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


@lru_cache(maxsize=1)
def pdf_font():
    root = Path(__file__).parent / "assets" / "fonts"
    regular = TTFont("CampusExport", str(root / "CampusExportSans-Regular.ttf"))
    pdfmetrics.registerFont(regular)
    pdfmetrics.registerFont(TTFont("CampusExportBold", str(root / "CampusExportSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("CampusLatin", str(root / "NotoSans-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("CampusLatinBold", str(root / "NotoSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(
        "CampusLatin",
        normal="CampusLatin",
        bold="CampusLatinBold",
        italic="CampusLatin",
        boldItalic="CampusLatinBold",
    )
    pdfmetrics.registerFontFamily(
        "CampusExport",
        normal="CampusExport",
        bold="CampusExportBold",
        italic="CampusExport",
        boldItalic="CampusExportBold",
    )
    return regular


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False


@dataclass
class Block:
    kind: str
    spans: list[Span] = field(default_factory=list)
    level: int = 0
    rows: list[list[list[Span]]] = field(default_factory=list)


def inline(tokens):
    spans, bold, italic, link = [], False, False, []
    for token in tokens or []:
        if token.type in ("strong_open", "strong_close"):
            bold = token.type == "strong_open"
        elif token.type in ("em_open", "em_close"):
            italic = token.type == "em_open"
        elif token.type == "link_open":
            link.append(token.attrGet("href") or "")
        elif token.type == "link_close":
            if link:
                spans.append(Span(f" ({link.pop()})"))
        elif token.type == "image":
            spans.append(Span(f"[图片：{token.content}；地址 {token.attrGet('src') or ''}]"))
        else:
            text = "\n" if token.type in ("softbreak", "hardbreak") else token.content
            spans.append(Span(text, bold, italic, token.type == "code_inline"))
    return spans


def parse(content):
    tokens = MarkdownIt("commonmark", {"html": False}).enable("table").parse(content)
    if len(tokens) > 12000:
        raise AppError(422, "EXPORT_TOO_COMPLEX", "内容结构过多，请分段导出。")
    blocks, lists = [], []
    heading, quote, prefix, table, row = 0, 0, "", None, None
    for token in tokens:
        kind = token.type
        if kind == "heading_open":
            heading = int(token.tag[1:])
        elif kind == "heading_close":
            heading = 0
        elif kind in ("ordered_list_open", "bullet_list_open"):
            lists.append([kind == "ordered_list_open", int(token.attrGet("start") or 1)])
        elif kind in ("ordered_list_close", "bullet_list_close"):
            lists.pop()
        elif kind == "list_item_open":
            ordered, index = lists[-1]
            prefix = f"{index}. " if ordered else "• "
            lists[-1][1] += 1
        elif kind == "list_item_close":
            prefix = ""
        elif kind == "blockquote_open":
            quote += 1
        elif kind == "blockquote_close":
            quote -= 1
        elif kind == "table_open":
            table = Block("table")
        elif kind == "tr_open":
            row = []
        elif kind == "tr_close":
            table.rows.append(row)
        elif kind == "table_close":
            if any(len(cells) > 12 for cells in table.rows) or len(table.rows) > 500:
                raise AppError(422, "EXPORT_TOO_COMPLEX", "表格过大，请拆分后导出。")
            blocks.append(table)
            table = None
        elif kind == "inline":
            spans = inline(token.children)
            if table is not None:
                row.append(spans)
            else:
                if prefix:
                    spans.insert(0, Span(prefix))
                    prefix = ""
                blocks.append(
                    Block(
                        "heading" if heading else "quote" if quote else "paragraph",
                        spans,
                        heading or len(lists),
                    )
                )
        elif kind in ("fence", "code_block"):
            blocks.append(Block("code", [Span(token.content.rstrip("\n"), code=True)], len(lists)))
        elif kind == "hr":
            blocks.append(Block("paragraph", [Span("——")]))
    return blocks


def word(title, blocks):
    document = Document()
    document.core_properties.title = title
    document.core_properties.author = ""
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.2)
    section.left_margin = section.right_margin = Cm(2.3)
    for name in ("Normal", "Title", *[f"Heading {i}" for i in range(1, 7)]):
        style = document.styles[name]
        style.font.name = "Calibri"
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    normal = document.styles["Normal"]
    normal.font.size = Pt(11)
    normal.paragraph_format.line_spacing = 1.35
    normal.paragraph_format.space_after = Pt(6)
    document.add_paragraph(title, "Title")

    def populate(paragraph, spans):
        for span in spans:
            run = paragraph.add_run(span.text)
            run.bold, run.italic = span.bold, span.italic
            if span.code:
                run.font.name, run.font.size = "Consolas", Pt(10)
                run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")

    for block in blocks:
        if block.kind == "table":
            table = document.add_table(rows=0, cols=len(block.rows[0]))
            table.style, table.autofit = "Table Grid", False
            for column in table.columns:
                column.width = Cm(16.4 / len(table.columns))
            for index, source in enumerate(block.rows):
                cells = table.add_row().cells
                for cell, spans in zip(cells, source, strict=True):
                    populate(cell.paragraphs[0], spans)
                    if index == 0:
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
                if index == 0:
                    repeat = OxmlElement("w:tblHeader")
                    table.rows[0]._tr.get_or_add_trPr().append(repeat)
            document.add_paragraph()
        else:
            paragraph = document.add_paragraph(
                style=f"Heading {block.level}" if block.kind == "heading" else "Normal"
            )
            if block.kind != "heading" and block.level:
                paragraph.paragraph_format.left_indent = Cm(min(block.level, 8) * 0.4)
            if block.kind == "quote":
                paragraph.paragraph_format.left_indent = Cm(0.6)
            populate(paragraph, block.spans)
    target = io.BytesIO()
    document.save(target)
    return target.getvalue()


def pdf(title, blocks):
    font = pdf_font()
    supported = font.face.charToGlyph.keys() | pdfmetrics.getFont("CampusLatin").face.charToGlyph.keys()
    spans = [Span(title)]
    for block in blocks:
        spans.extend(block.spans)
        spans.extend(span for row in block.rows for cell in row for span in cell)
    if any(ord(char) not in supported for span in spans for char in span.text if not char.isspace()):
        raise AppError(
            422, "EXPORT_UNSUPPORTED_CHARACTERS", "PDF 字体暂不支持部分字符，请改用 Word 或 Markdown 导出。"
        )
    target = io.BytesIO()
    document = SimpleDocTemplate(
        target,
        pagesize=A4,
        leftMargin=65,
        rightMargin=65,
        topMargin=55,
        bottomMargin=55,
        title=title,
        author="",
    )
    body = ParagraphStyle(
        "Body", fontName="CampusExport", fontSize=11, leading=17, wordWrap="CJK", spaceAfter=7
    )
    title_style = ParagraphStyle("Title", parent=body, fontSize=20, leading=28, spaceAfter=18)
    cell_style = ParagraphStyle("Cell", parent=body, fontSize=10, leading=15, spaceAfter=0)

    def markup(spans):
        parts = []
        for span in spans:
            segments = []
            for fallback, chars in groupby(
                span.text, lambda char: not char.isspace() and ord(char) not in font.face.charToGlyph
            ):
                value = escape("".join(chars)).replace("\n", "<br/>")
                if span.code:
                    value = value.replace(" ", "&#160;").replace("\t", "&#160;" * 4)
                segments.append(f'<font name="CampusLatin">{value}</font>' if fallback else value)
            value = "".join(segments)
            if span.bold:
                value = f"<b>{value}</b>"
            if span.italic:
                value = f"<i>{value}</i>"
            parts.append(value)
        return "".join(parts) or " "

    story = [Paragraph(markup([Span(title)]), title_style)]
    for block in blocks:
        if block.kind == "table":
            rows = [[Paragraph(markup(cell), cell_style) for cell in row] for row in block.rows]
            table = Table(
                rows,
                colWidths=[document.width / len(rows[0])] * len(rows[0]),
                repeatRows=1,
                splitInRow=1,
                hAlign="LEFT",
            )
            table.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9D9D9")),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 7),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            story.extend([table, Spacer(1, 10)])
        else:
            style = body
            if block.kind == "heading":
                size = max(11, 18 - block.level)
                style = ParagraphStyle(
                    "Heading", parent=body, fontSize=size, leading=size + 7, spaceBefore=10, keepWithNext=True
                )
            elif block.level or block.kind == "quote":
                style = ParagraphStyle("Indented", parent=body, leftIndent=min(block.level or 1, 8) * 10)
            story.append(Paragraph(markup(block.spans), style))

    def footer(canvas, doc):
        if doc.page > 100:
            raise AppError(422, "EXPORT_TOO_COMPLEX", "页数超过 100 页，请分段导出。")
        label = Paragraph(str(doc.page), ParagraphStyle("Footer", parent=body, alignment=TA_CENTER))
        label.wrap(document.width, 20)
        label.drawOn(canvas, document.leftMargin, 26)

    try:
        document.build(story, onFirstPage=footer, onLaterPages=footer)
    except LayoutError:
        raise AppError(422, "EXPORT_TOO_COMPLEX", "内容无法完整排版，请拆分过长表格后导出。") from None
    return target.getvalue()


def render(title, content, format):
    if not content.strip():
        raise AppError(422, "EMPTY_EXPORT", "请先填写或生成教学内容。")
    if INVALID_XML.search(title + content):
        raise AppError(422, "INVALID_EXPORT_TEXT", "内容含有不支持的控制字符，请移除后导出。")
    if format == "md":
        return content.encode("utf-8")
    blocks = parse(content)
    return word(title, blocks) if format == "docx" else pdf(title, blocks)
