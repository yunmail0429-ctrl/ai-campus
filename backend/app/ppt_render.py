"""Bounded, editable slide content; generated text never becomes executable markup."""

import io

from pptx import Presentation
from pptx.chart.data import XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChartSeries(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=24)
    points: list[tuple[float, float]] = Field(min_length=9, max_length=30)

    @model_validator(mode="after")
    def valid_points(self):
        xs = [point[0] for point in self.points]
        if any(abs(value) > 1000000 for point in self.points for value in point):
            raise ValueError("Chart coordinates are out of range")
        if xs != sorted(xs) or len(xs) != len(set(xs)):
            raise ValueError("Chart x coordinates must be unique and increasing")
        return self


class ChartContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x_axis: str = Field(default="x", min_length=1, max_length=24)
    y_axis: str = Field(default="y", min_length=1, max_length=24)
    series: list[ChartSeries] = Field(min_length=1, max_length=2)


class SlideContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=48)
    points: list[str] = Field(min_length=1, max_length=5)
    notes: str = Field(default="", max_length=3000)
    chart: ChartContent | None = None


class DeckContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slides: list[SlideContent] = Field(min_length=1, max_length=60)


def render_deck(deck: DeckContent) -> bytes:
    presentation = Presentation()
    presentation.slide_width, presentation.slide_height = Inches(13.333), Inches(7.5)
    for index, content in enumerate(deck.slides, 1):
        if any(not point.strip() or len(point) > 100 for point in content.points):
            raise ValueError("Slide points must contain 1 to 100 characters")
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor(248, 250, 252)

        def text_box(x, y, width, height, text, size, color, bold=False, target=slide):
            box = target.shapes.add_textbox(Inches(x), Inches(y), Inches(width), Inches(height))
            frame = box.text_frame
            frame.word_wrap = True
            frame.margin_left = frame.margin_right = Inches(0.05)
            paragraph = frame.paragraphs[0]
            # Model-supplied hard breaks must not bypass the character-based layout bounds.
            # Speaker notes retain their original paragraph structure below.
            paragraph.text = " ".join(text.split())
            paragraph.font.name = "Microsoft YaHei"
            paragraph.font.size = Pt(size)
            paragraph.font.bold = bold
            paragraph.font.color.rgb = RGBColor(*color)
            return box

        text_box(0.65, 0.45, 12, 1.1, content.title, 30, (20, 43, 70), True)
        # Keep every model point: overflow goes into speaker notes with a visible cue.
        visual = not content.chart and len(content.points) <= 3 and all(len(point) <= 50 for point in content.points)
        visible_points = content.points[:3] if content.chart else content.points
        overflow_points = content.points[3:] if content.chart else []
        row_height = (4.9 if content.chart and overflow_points else 5.3) / len(visible_points)
        for position, point in enumerate(visible_points):
            text_box(
                0.7, 1.65 + position * row_height, 0.55, 0.5, f"{position + 1:02}", 16, (46, 106, 154), True
            )
            text_box(
                1.4, 1.58 + position * row_height,
                4.1 if content.chart else (5.5 if visual else 11.1), row_height,
                point, (14 if len(point) > 50 else 17) if content.chart else 20, (40, 50, 65),
            )
        if visual:
            # Native PowerPoint shapes form an editable illustration without an image service.
            panel = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(7.4), Inches(1.7), Inches(5.1), Inches(4.9))
            panel.fill.solid()
            panel.fill.fore_color.rgb = RGBColor(226, 239, 247)
            panel.line.fill.background()
            for x, y, size, color in (
                (8.1, 2.2, 1.7, (100, 174, 194)),
                (9.7, 2.7, 2.1, (66, 133, 172)),
                (8.9, 4.1, 1.5, (244, 180, 106)),
            ):
                circle = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(size), Inches(size))
                circle.fill.solid()
                circle.fill.fore_color.rgb = RGBColor(*color)
                circle.line.fill.background()
            text_box(8.05, 5.9, 3.9, 0.4, "观察 · 思考 · 探索", 16, (35, 82, 111), True)
        if overflow_points:
            text_box(1.4, 6.85, 4.1, 0.3, f"另有 {len(overflow_points)} 条要点见讲解备注", 11, (90, 105, 120))
        if content.chart:
            chart_data = XyChartData()
            for source in content.chart.series:
                series = chart_data.add_series(source.name)
                for x, y in source.points:
                    series.add_data_point(x, y)
            chart = slide.shapes.add_chart(
                XL_CHART_TYPE.XY_SCATTER_LINES_NO_MARKERS,
                Inches(5.8), Inches(1.55), Inches(6.65), Inches(5.3), chart_data,
            ).chart
            chart.has_legend = len(content.chart.series) > 1
            chart.category_axis.has_title = True
            chart.category_axis.axis_title.text_frame.text = content.chart.x_axis
            chart.value_axis.has_title = True
            chart.value_axis.axis_title.text_frame.text = content.chart.y_axis
        text_box(11.5, 7.05, 1.2, 0.3, f"{index} / {len(deck.slides)}", 11, (90, 105, 120))
        notes = content.notes
        if overflow_points:
            notes += "\n\n本页补充要点：\n" + "\n".join(
                f"{index}. {point}" for index, point in enumerate(overflow_points, 4)
            )
        slide.notes_slide.notes_text_frame.text = notes
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()
