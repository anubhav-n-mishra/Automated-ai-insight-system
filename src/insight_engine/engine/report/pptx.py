"""PowerPoint renderer.

Every slide is built from :class:`~insight_engine.domain.results.AnalysisResult`
and nothing else, which is what fixes the previous renderer's central bug: it
read ``current_totals``/``previous_totals`` keys the engine never produced, so
the comparison chart printed "Insufficient data" on every deck ever generated.

The deck deliberately carries a methodology slide. A report a stakeholder
cannot audit is a report they are right not to trust.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.presentation import Presentation as PresentationType
from pptx.slide import Slide
from pptx.util import Inches, Pt

from insight_engine.core.errors import RenderError
from insight_engine.core.logging import get_logger
from insight_engine.domain.results import AnalysisResult
from insight_engine.engine.formatting import (
    arrow,
    compact_number,
    format_delta_pct,
    format_value,
)
from insight_engine.engine.report.theme import DEFAULT_THEME, Theme

logger = get_logger("engine.report.pptx")

SLIDE_WIDTH = Inches(13.333)
SLIDE_HEIGHT = Inches(7.5)
MARGIN = Inches(0.6)
CONTENT_WIDTH = Inches(12.133)
BLANK_LAYOUT = 6

MAX_TABLE_ROWS = 8
MAX_CHART_CATEGORIES = 6


def build_deck(
    result: AnalysisResult,
    output_dir: Path,
    *,
    theme: Theme = DEFAULT_THEME,
    dashboard_url: str | None = None,
    qr_png: bytes | None = None,
    filename: str | None = None,
) -> Path:
    """Render ``result`` to a ``.pptx`` and return the path written."""
    try:
        presentation = _new_presentation()
        narrative = result.narrative

        _cover(presentation, result, theme)
        _executive_summary(presentation, result, theme)
        _metric_comparison(presentation, result, theme)
        if result.drivers:
            _drivers(presentation, result, theme)
        if result.insights:
            _insight_table(presentation, result, theme)
        if narrative and narrative.bullets:
            _bullets(presentation, narrative.bullets, theme, title="What Changed")
        if narrative and narrative.recommendation:
            _recommendation(presentation, narrative.recommendation, theme)
        _methodology(presentation, result, theme)
        if qr_png and dashboard_url:
            _dashboard_qr(presentation, dashboard_url, qr_png, theme)

        output_dir.mkdir(parents=True, exist_ok=True)
        name = filename or _default_filename()
        path = output_dir / name
        presentation.save(str(path))
    except RenderError:
        raise
    except Exception as error:
        raise RenderError(
            "Could not render the PowerPoint report",
            internal_detail=f"{type(error).__name__}: {error}",
        ) from error

    logger.info(
        "deck rendered", extra={"file": path.name, "slides": len(presentation.slides._sldIdLst)}
    )
    return path


def _default_filename() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"insight-report-{stamp}-{uuid.uuid4().hex[:8]}.pptx"


def _new_presentation() -> PresentationType:
    presentation = Presentation()
    presentation.slide_width = SLIDE_WIDTH
    presentation.slide_height = SLIDE_HEIGHT
    return presentation


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def _slide(presentation: PresentationType) -> Slide:
    return presentation.slides.add_slide(presentation.slide_layouts[BLANK_LAYOUT])


def _rect(
    slide: Slide, x: float, y: float, w: float, h: float, fill: RGBColor, *, radius: bool = False
) -> Any:
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        Inches(x),
        Inches(y),
        Inches(w),
        Inches(h),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _text(
    slide: Slide,
    x: float,
    y: float,
    w: float,
    h: float,
    content: str,
    *,
    size: int = 14,
    bold: bool = False,
    color: RGBColor | None = None,
    align: PP_ALIGN = PP_ALIGN.LEFT,
    font: str = "Calibri",
    italic: bool = False,
) -> Any:
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    paragraph.text = content
    paragraph.alignment = align
    run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.name = font
    if color is not None:
        run.font.color.rgb = color
    return box


def _header(slide: Slide, title: str, theme: Theme, *, subtitle: str | None = None) -> None:
    _rect(slide, 0, 0, 13.333, 0.95, theme.color(theme.primary))
    _text(
        slide,
        0.6,
        0.2,
        12.1,
        0.55,
        title,
        size=26,
        bold=True,
        color=theme.color(theme.text_inverse),
        font=theme.heading_font,
    )
    if subtitle:
        _text(
            slide,
            0.6,
            1.05,
            12.1,
            0.35,
            subtitle,
            size=12,
            color=theme.color(theme.text_muted),
            font=theme.body_font,
        )


def _footnote(slide: Slide, text: str, theme: Theme) -> None:
    _text(
        slide,
        0.6,
        6.95,
        12.1,
        0.35,
        text,
        size=9,
        color=theme.color(theme.text_muted),
        font=theme.body_font,
        italic=True,
    )


# --------------------------------------------------------------------------- #
# Slides
# --------------------------------------------------------------------------- #
def _cover(presentation: PresentationType, result: AnalysisResult, theme: Theme) -> None:
    slide = _slide(presentation)
    _rect(slide, 0, 0, 13.333, 7.5, theme.color(theme.primary_dark))
    _rect(slide, 0, 0, 13.333, 0.18, theme.color(theme.accent))

    title = result.narrative.title if result.narrative else result.spec_title
    _text(
        slide,
        1.0,
        2.4,
        11.333,
        1.4,
        title,
        size=42,
        bold=True,
        color=theme.color(theme.text_inverse),
        align=PP_ALIGN.CENTER,
        font=theme.heading_font,
    )
    _text(
        slide,
        1.0,
        3.9,
        11.333,
        0.6,
        result.period.describe(),
        size=20,
        color=theme.color("#B9C6E4"),
        align=PP_ALIGN.CENTER,
        font=theme.body_font,
    )
    _text(
        slide,
        1.0,
        4.6,
        11.333,
        0.5,
        f"{result.row_count:,} rows analysed across {result.segment_count:,} segment(s)",
        size=14,
        color=theme.color("#B9C6E4"),
        align=PP_ALIGN.CENTER,
        font=theme.body_font,
    )
    _text(
        slide,
        1.0,
        6.5,
        11.333,
        0.4,
        f"Generated {result.generated_at.strftime('%d %B %Y at %H:%M UTC')}",
        size=11,
        color=theme.color("#8FA1C9"),
        align=PP_ALIGN.CENTER,
        font=theme.body_font,
    )


def _executive_summary(
    presentation: PresentationType, result: AnalysisResult, theme: Theme
) -> None:
    slide = _slide(presentation)
    _header(slide, "Executive Summary", theme)

    headline = result.narrative.headline if result.narrative else ""
    if headline:
        _rect(slide, 0.6, 1.25, 12.133, 1.15, theme.color(theme.surface_muted), radius=True)
        _text(
            slide,
            0.85,
            1.45,
            11.6,
            0.85,
            headline,
            size=19,
            bold=True,
            color=theme.color(theme.text),
            font=theme.heading_font,
        )

    cards = result.top_movers(limit=4)
    if cards:
        count = len(cards)
        card_w = 2.85
        gap = 0.3
        total = count * card_w + (count - 1) * gap
        start_x = (13.333 - total) / 2

        for index, total_metric in enumerate(cards):
            x = start_x + index * (card_w + gap)
            _rect(slide, x, 2.75, card_w, 2.15, theme.color(theme.surface_muted), radius=True)
            _rect(slide, x, 2.75, card_w, 0.08, theme.sentiment_color(total_metric.sentiment))

            _text(
                slide,
                x + 0.12,
                3.0,
                card_w - 0.24,
                0.4,
                total_metric.label.upper(),
                size=11,
                bold=True,
                color=theme.color(theme.text_muted),
                align=PP_ALIGN.CENTER,
                font=theme.body_font,
            )
            _text(
                slide,
                x + 0.12,
                3.4,
                card_w - 0.24,
                0.75,
                format_value(
                    total_metric.current, total_metric.unit, precision=total_metric.precision
                ),
                size=28,
                bold=True,
                color=theme.color(theme.text),
                align=PP_ALIGN.CENTER,
                font=theme.heading_font,
            )
            _text(
                slide,
                x + 0.12,
                4.15,
                card_w - 0.24,
                0.35,
                f"{arrow(total_metric.direction)} {format_delta_pct(total_metric.delta_pct)}",
                size=15,
                bold=True,
                color=theme.sentiment_color(total_metric.sentiment),
                align=PP_ALIGN.CENTER,
                font=theme.body_font,
            )
            baseline = format_value(
                total_metric.previous, total_metric.unit, precision=total_metric.precision
            )
            _text(
                slide,
                x + 0.12,
                4.5,
                card_w - 0.24,
                0.32,
                f"from {baseline}",
                size=10,
                color=theme.color(theme.text_muted),
                align=PP_ALIGN.CENTER,
                font=theme.body_font,
            )

    if result.warnings:
        _rect(slide, 0.6, 5.2, 12.133, 1.5, theme.color("#FFF6E6"), radius=True)
        _text(
            slide,
            0.85,
            5.3,
            11.6,
            0.3,
            "Read with care",
            size=12,
            bold=True,
            color=theme.color(theme.accent),
            font=theme.heading_font,
        )
        box = slide.shapes.add_textbox(Inches(0.85), Inches(5.6), Inches(11.6), Inches(1.05))
        frame = box.text_frame
        frame.word_wrap = True
        for index, warning in enumerate(result.warnings[:3]):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.text = f"• {warning}"
            run = paragraph.runs[0]
            run.font.size = Pt(10)
            run.font.name = theme.body_font
            run.font.color.rgb = theme.color(theme.text_muted)


def _metric_comparison(
    presentation: PresentationType, result: AnalysisResult, theme: Theme
) -> None:
    """Grouped bars, current vs previous, for the additive metrics."""
    slide = _slide(presentation)
    _header(slide, "Metric Comparison", theme, subtitle=result.period.describe())

    # Ratios live on a different scale from counts; plotting them together makes
    # both unreadable, so they are shown in the totals table instead.
    plottable = [total for total in result.totals if total.unit not in {"percent", "ratio"}][
        :MAX_CHART_CATEGORIES
    ]

    if not plottable:
        _text(
            slide,
            0.6,
            3.2,
            12.133,
            1.0,
            "No additive metrics to chart. Ratio metrics are shown in the insight table.",
            size=16,
            color=theme.color(theme.text_muted),
            align=PP_ALIGN.CENTER,
            font=theme.body_font,
        )
        return

    chart_data = CategoryChartData()
    chart_data.categories = [total.label for total in plottable]
    chart_data.add_series("Previous", [total.previous for total in plottable])
    chart_data.add_series("Current", [total.current for total in plottable])

    frame = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(0.6),
        Inches(1.5),
        Inches(12.133),
        Inches(4.4),
        chart_data,
    )
    chart = frame.chart
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False

    plot = chart.plots[0]
    plot.gap_width = 60
    for index, series in enumerate(plot.series):
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = (
            theme.color(theme.text_muted) if index == 0 else theme.color(theme.primary)
        )

    summary = "   ".join(
        f"{total.label} {arrow(total.direction)} {format_delta_pct(total.delta_pct)}"
        for total in plottable[:5]
    )
    _text(
        slide,
        0.6,
        6.05,
        12.133,
        0.5,
        summary,
        size=12,
        color=theme.color(theme.text),
        align=PP_ALIGN.CENTER,
        font=theme.body_font,
    )
    _footnote(
        slide,
        "Bars show period totals. Ratio metrics are excluded because they do not share the "
        "scale of count metrics.",
        theme,
    )


def _drivers(presentation: PresentationType, result: AnalysisResult, theme: Theme) -> None:
    """Which segments account for the largest metric's movement."""
    attribution = result.drivers[0]
    slide = _slide(presentation)
    _header(
        slide,
        f"What Moved {attribution.label}",
        theme,
        subtitle=(
            f"Net movement {compact_number(attribution.total_delta)}; the segments below "
            f"account for {attribution.explained_pct:.0f}% of all segment movement "
            f"across {attribution.segment_count} segments"
            + (
                ". Gains and losses partly cancel, so the net understates the churn beneath it"
                if attribution.offsetting
                else ""
            )
        ),
    )

    drivers = attribution.drivers[:MAX_CHART_CATEGORIES]
    if not drivers:
        return

    chart_data = CategoryChartData()
    chart_data.categories = [driver.segment_label for driver in reversed(drivers)]
    chart_data.add_series("Change", [driver.delta for driver in reversed(drivers)])

    frame = slide.shapes.add_chart(
        XL_CHART_TYPE.BAR_CLUSTERED,
        Inches(0.6),
        Inches(1.6),
        Inches(12.133),
        Inches(4.6),
        chart_data,
    )
    chart = frame.chart
    chart.has_legend = False
    series = chart.plots[0].series[0]
    for index, point in enumerate(series.points):
        driver = list(reversed(drivers))[index]
        point.format.fill.solid()
        point.format.fill.fore_color.rgb = theme.sentiment_color(driver.sentiment)

    _footnote(
        slide,
        "Bars are absolute change against the previous period. Colour reflects whether the "
        "movement is favourable for this metric, not its sign.",
        theme,
    )


def _insight_table(presentation: PresentationType, result: AnalysisResult, theme: Theme) -> None:
    slide = _slide(presentation)
    _header(
        slide,
        "Top Movements by Impact",
        theme,
        subtitle=f"Ranked across {result.segment_count:,} segments",
    )

    rows = result.insights[:MAX_TABLE_ROWS]
    table_shape = slide.shapes.add_table(
        len(rows) + 1, 5, Inches(0.6), Inches(1.5), Inches(12.133), Inches(0.42 * (len(rows) + 1))
    )
    table = table_shape.table
    for width, index in zip((4.0, 1.9, 2.1, 2.1, 2.033), range(5), strict=True):
        table.columns[index].width = Inches(width)

    headers = ("Segment", "Metric", "Previous", "Current", "Change")
    for index, label in enumerate(headers):
        cell = table.cell(0, index)
        cell.text = label
        cell.fill.solid()
        cell.fill.fore_color.rgb = theme.color(theme.primary)
        paragraph = cell.text_frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.CENTER if index else PP_ALIGN.LEFT
        run = paragraph.runs[0]
        run.font.size = Pt(12)
        run.font.bold = True
        run.font.name = theme.heading_font
        run.font.color.rgb = theme.color(theme.text_inverse)

    for row_index, insight in enumerate(rows, start=1):
        values = (
            insight.segment_label,
            insight.label,
            format_value(insight.previous_value, insight.unit, precision=insight.precision),
            format_value(insight.current_value, insight.unit, precision=insight.precision),
            f"{arrow(insight.direction)} {format_delta_pct(insight.delta_pct)}",
        )
        for column_index, value in enumerate(values):
            cell = table.cell(row_index, column_index)
            cell.text = value
            cell.fill.solid()
            cell.fill.fore_color.rgb = theme.color(
                theme.surface_muted if row_index % 2 else theme.surface
            )
            paragraph = cell.text_frame.paragraphs[0]
            paragraph.alignment = PP_ALIGN.CENTER if column_index else PP_ALIGN.LEFT
            run = paragraph.runs[0]
            run.font.size = Pt(11)
            run.font.name = theme.body_font
            if column_index == 4:
                run.font.bold = True
                run.font.color.rgb = theme.sentiment_color(insight.sentiment)
            else:
                run.font.color.rgb = theme.color(theme.text)

    _footnote(
        slide,
        "Impact combines a segment's share of the total movement, the metric's priority and "
        'the segment\'s materiality. "n/a" means the previous period was zero, so a '
        "percentage change is undefined.",
        theme,
    )


def _bullets(
    presentation: PresentationType, bullets: list[str], theme: Theme, *, title: str
) -> None:
    slide = _slide(presentation)
    _header(slide, title, theme)

    box = slide.shapes.add_textbox(Inches(0.9), Inches(1.7), Inches(11.5), Inches(5.0))
    frame = box.text_frame
    frame.word_wrap = True
    for index, text in enumerate(bullets[:6]):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.text = f"•  {text}"
        paragraph.space_after = Pt(16)
        run = paragraph.runs[0]
        run.font.size = Pt(17)
        run.font.name = theme.body_font
        run.font.color.rgb = theme.color(theme.text)


def _recommendation(presentation: PresentationType, recommendation: str, theme: Theme) -> None:
    slide = _slide(presentation)
    _header(slide, "Recommended Next Step", theme)
    _rect(slide, 1.2, 2.3, 10.933, 2.6, theme.color(theme.surface_muted), radius=True)
    _rect(slide, 1.2, 2.3, 0.1, 2.6, theme.color(theme.accent))
    _text(
        slide,
        1.7,
        2.9,
        10.0,
        1.6,
        recommendation,
        size=22,
        color=theme.color(theme.text),
        align=PP_ALIGN.LEFT,
        font=theme.heading_font,
    )


def _methodology(presentation: PresentationType, result: AnalysisResult, theme: Theme) -> None:
    """How the numbers were produced. A report nobody can audit is not evidence."""
    slide = _slide(presentation)
    _header(slide, "Methodology and Coverage", theme)

    period = result.period
    lines = [
        f"Current period: {period.current_start} to {period.current_end} "
        f"({period.current_days} days, {period.current_rows:,} rows aggregated).",
        f"Previous period: {period.previous_start} to {period.previous_end} "
        f"({period.previous_days} days, {period.previous_rows:,} rows aggregated).",
        f"Rows read from source: {result.row_count:,}. "
        f"Segments compared: {result.segment_count:,}.",
        (
            f"Segmented by: {', '.join(result.dimensions)}."
            if result.dimensions
            else "No segmentation: totals only."
        ),
        "Ratio metrics are computed from aggregated numerators and denominators, not averaged "
        "across rows.",
        "Percentage change is undefined when the previous value is zero and is reported as "
        "“n/a” rather than 100%.",
        (
            f"Narrative written by {result.narrative.provider}"
            + (
                f" ({result.narrative.model})"
                if result.narrative and result.narrative.model
                else ""
            )
            + "."
            if result.narrative
            else "No narrative was generated."
        ),
        f"Analysis completed in {result.duration_ms / 1000:.2f} seconds.",
    ]
    lines.extend(f"Caveat: {warning}" for warning in result.warnings[:3])

    box = slide.shapes.add_textbox(Inches(0.8), Inches(1.5), Inches(11.7), Inches(5.3))
    frame = box.text_frame
    frame.word_wrap = True
    for index, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.text = f"•  {line}"
        paragraph.space_after = Pt(9)
        run = paragraph.runs[0]
        run.font.size = Pt(12)
        run.font.name = theme.body_font
        run.font.color.rgb = theme.color(theme.text)


def _dashboard_qr(
    presentation: PresentationType, dashboard_url: str, qr_png: bytes, theme: Theme
) -> None:
    import io

    slide = _slide(presentation)
    _header(slide, "Live Dashboard", theme)

    slide.shapes.add_picture(
        io.BytesIO(qr_png), Inches(4.92), Inches(1.5), Inches(3.5), Inches(3.5)
    )
    _text(
        slide,
        0.8,
        5.2,
        11.7,
        0.5,
        "Scan to open the interactive dashboard with the full segment breakdown "
        "and an audio briefing.",
        size=16,
        color=theme.color(theme.text),
        align=PP_ALIGN.CENTER,
        font=theme.body_font,
    )
    display = dashboard_url if len(dashboard_url) <= 90 else dashboard_url[:87] + "..."
    _text(
        slide,
        0.8,
        5.75,
        11.7,
        0.4,
        display,
        size=10,
        color=theme.color(theme.text_muted),
        align=PP_ALIGN.CENTER,
        font=theme.body_font,
    )
    _footnote(slide, "The link carries an access token and expires with the session.", theme)
