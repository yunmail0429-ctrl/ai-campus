import io

import pytest
from pptx import Presentation

from app.ppt_render import DeckContent, render_deck
from app.resource_contracts import ResourceArtifact
from app.resource_execution import extension


def test_generated_deck_has_editable_text_notes_and_valid_container():
    deck = DeckContent.model_validate(
        {
            "slides": [
                {
                    "title": "函数单调性",
                    "points": ["先观察图像", "再用定义证明"],
                    "notes": "组织学生分组讨论。",
                },
                {"title": "课堂练习", "points": ["证明 f(x)=2x+1 在实数集上递增。"]},
            ]
        }
    )
    result = render_deck(deck)
    presentation = Presentation(io.BytesIO(result))
    assert len(presentation.slides) == 2
    assert "先观察图像" in [shape.text for shape in presentation.slides[0].shapes if shape.has_text_frame]
    assert presentation.slides[0].notes_slide.notes_text_frame.text == "组织学生分组讨论。"
    assert sum(shape.shape_type == 1 for shape in presentation.slides[0].shapes) >= 4
    assert all(
        shape.left + shape.width <= presentation.slide_width
        for slide in presentation.slides
        for shape in slide.shapes
    )
    artifact = ResourceArtifact(
        content=result, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert extension("ppt", artifact) == "pptx"


@pytest.mark.parametrize("point", [" ", "字" * 101])
def test_reject_content_that_cannot_fit_instead_of_silent_truncation(point):
    with pytest.raises(ValueError):
        render_deck(DeckContent.model_validate({"slides": [{"title": "标题", "points": [point]}]}))


def test_model_line_breaks_do_not_expand_bounded_text_into_unbounded_lines():
    deck = DeckContent.model_validate(
        {
            "slides": [
                {
                    "title": "标题\n第二行\n第三行",
                    "points": ["内容\n" * 30],
                    "notes": "备注第一行\n备注第二行",
                }
            ]
        }
    )
    slide = Presentation(io.BytesIO(render_deck(deck))).slides[0]
    texts = [shape.text for shape in slide.shapes if shape.has_text_frame]
    assert all("\n" not in text and "\v" not in text for text in texts)
    assert slide.notes_slide.notes_text_frame.text == "备注第一行\n备注第二行"


def test_function_graph_is_an_editable_chart_with_bounded_data():
    slide_data = {
        "title": "观察二次函数图像",
        "points": ["观察最低点和对称轴", "比较 x 增加时 y 的变化"],
        "chart": {
            "x_axis": "x",
            "y_axis": "y=x²",
            "series": [{"name": "y=x²", "points": [
                [-2, 4], [-1.5, 2.25], [-1, 1], [-0.5, 0.25], [0, 0],
                [0.5, 0.25], [1, 1], [1.5, 2.25], [2, 4],
            ]}],
        },
    }
    presentation = Presentation(io.BytesIO(render_deck(DeckContent.model_validate({"slides": [slide_data]}))))
    charts = [shape.chart for shape in presentation.slides[0].shapes if shape.has_chart]
    assert len(charts) == 1
    assert charts[0].series[0].values == (4.0, 2.25, 1.0, 0.25, 0.0, 0.25, 1.0, 2.25, 4.0)
    assert charts[0].value_axis.axis_title.text_frame.text == "y=x²"
    assert any("最低点" in shape.text for shape in presentation.slides[0].shapes if shape.has_text_frame)
    assert all(
        shape.left + shape.width <= presentation.slide_width
        for shape in presentation.slides[0].shapes
    )
    invalid = {**slide_data, "chart": {**slide_data["chart"], "series": [
        {"name": "乱序", "points": list(reversed(slide_data["chart"]["series"][0]["points"]))}
    ]}}
    with pytest.raises(ValueError):
        DeckContent.model_validate({"slides": [invalid]})


def test_chart_slide_moves_extra_points_to_notes_without_losing_them():
    points = ["第一条要点", "第二条要点", "第三条要点", "第四条完整内容", "第五条完整内容"]
    chart = {"series": [{"name": "y=x²", "points": [
        [-2, 4], [-1.5, 2.25], [-1, 1], [-0.5, 0.25], [0, 0],
        [0.5, 0.25], [1, 1], [1.5, 2.25], [2, 4],
    ]}]}
    deck = DeckContent.model_validate({"slides": [{
        "title": "图像观察", "points": points, "notes": "原始讲解备注", "chart": chart,
    }]})
    slide = Presentation(io.BytesIO(render_deck(deck))).slides[0]
    visible = "\n".join(shape.text for shape in slide.shapes if shape.has_text_frame)
    notes = slide.notes_slide.notes_text_frame.text
    assert all(point in visible for point in points[:3])
    assert "另有 2 条要点见讲解备注" in visible
    assert all(point in notes for point in points[3:])
    assert "原始讲解备注" in notes
    assert any(shape.has_chart for shape in slide.shapes)
