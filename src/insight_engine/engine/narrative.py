"""Prose generation.

Two writers, one interface:

``template``
    Deterministic, offline, always available. Built straight from the computed
    numbers, so it is never wrong about them.
``provider``
    An LLM asked for a strict JSON object, given only aggregated figures.

The template writer is not a degraded mode to apologise for — it is the floor
that makes the LLM optional, which matters for deployments that cannot send
business metrics to a third party. Every provider response is validated and
falls back to the template on any problem.
"""

from __future__ import annotations

import json
import re
from typing import Any

from insight_engine.core.logging import get_logger
from insight_engine.domain.results import AnalysisResult, Narrative
from insight_engine.engine.formatting import (
    describe_insight,
    describe_total,
    format_delta_pct,
    format_value,
)
from insight_engine.llm.base import CompletionRequest, NarrativeProvider

logger = get_logger("engine.narrative")

MAX_BULLETS = 5
MAX_FIELD_CHARS = 600

SYSTEM_PROMPT = (
    "You are a senior business analyst writing an executive summary. "
    "You receive metric movements that have already been computed and verified. "
    "Rules: use only the numbers provided; never invent a figure, a cause or a "
    "comparison; do not speculate about why something moved unless the data names "
    "a segment; prefer plain language over jargon. "
    "Respond with a single JSON object and nothing else."
)

USER_TEMPLATE = """\
Write an executive summary for this report.

REPORTING PERIOD
{period}

OVERALL MOVEMENTS
{totals}

LARGEST SEGMENT MOVEMENTS (already ranked by impact)
{insights}

WHAT DROVE THE TOTALS
{drivers}

{caveats}
Return exactly this JSON shape:
{{
  "title": "short report title, under 60 characters",
  "headline": "one sentence naming the single most important movement, with its number",
  "bullets": ["3 to 5 findings, each citing a specific figure from above"],
  "recommendation": "one concrete, actionable next step that follows from the data"
}}
"""


def _period_block(result: AnalysisResult) -> str:
    period = result.period
    lines = [
        f"Current: {period.current_start} to {period.current_end} "
        f"({period.current_days} days, {period.current_rows:,} rows)",
        f"Previous: {period.previous_start} to {period.previous_end} "
        f"({period.previous_days} days, {period.previous_rows:,} rows)",
    ]
    if result.dimensions:
        lines.append(f"Segmented by: {', '.join(result.dimensions)}")
    return "\n".join(lines)


def _totals_block(result: AnalysisResult) -> str:
    if not result.totals:
        return "(none)"
    return "\n".join(f"- {describe_total(total)}" for total in result.totals)


def _insights_block(result: AnalysisResult, limit: int = 10) -> str:
    if not result.insights:
        return "(no segment moved materially)"
    return "\n".join(f"- {describe_insight(insight)}" for insight in result.insights[:limit])


def _drivers_block(result: AnalysisResult, limit: int = 4) -> str:
    if not result.drivers:
        return "(not decomposed)"
    lines = []
    for attribution in result.drivers[:limit]:
        names = ", ".join(driver.segment_label for driver in attribution.drivers[:3])
        note = (
            " (gains and losses partly cancel, so the net understates the underlying churn)"
            if attribution.offsetting
            else ""
        )
        lines.append(
            f"- {attribution.label} moved by {attribution.total_delta:,.2f} net; "
            f"the largest contributors were {names} "
            f"({attribution.explained_pct:.0f}% of all segment movement, "
            f"{attribution.segment_count} segments in total){note}"
        )
    return "\n".join(lines)


def build_prompt(result: AnalysisResult) -> CompletionRequest:
    """Assemble the provider request from computed figures only."""
    caveats = ""
    if result.warnings:
        caveats = (
            "CAVEATS THE SUMMARY MUST RESPECT\n"
            + "\n".join(f"- {warning}" for warning in result.warnings)
            + "\n\n"
        )

    user = USER_TEMPLATE.format(
        period=_period_block(result),
        totals=_totals_block(result),
        insights=_insights_block(result),
        drivers=_drivers_block(result),
        caveats=caveats,
    )
    return CompletionRequest(system=SYSTEM_PROMPT, user=user)


_FENCE = re.compile(r"^```[a-zA-Z]*\n?|\n?```$")


def parse_response(raw: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response.

    Models emit fenced blocks or a sentence of preamble even when told not to,
    so strip fences first and fall back to the outermost brace pair.
    """
    text = _FENCE.sub("", raw.strip()).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("response contains no JSON object") from None
        parsed = json.loads(text[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("response JSON is not an object")
    return parsed


def _clean(value: object, *, limit: int = MAX_FIELD_CHARS) -> str:
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def narrative_from_response(
    raw: str, *, provider: str, model: str, fallback_title: str
) -> Narrative:
    """Validate a provider response into a :class:`Narrative`."""
    data = parse_response(raw)

    bullets_raw = data.get("bullets") or []
    if isinstance(bullets_raw, str):
        bullets_raw = [bullets_raw]
    bullets = [_clean(bullet) for bullet in list(bullets_raw)[:MAX_BULLETS] if str(bullet).strip()]

    headline = _clean(data.get("headline", ""), limit=300)
    if not headline:
        raise ValueError("response is missing a headline")

    return Narrative(
        title=_clean(data.get("title") or fallback_title, limit=120),
        headline=headline,
        bullets=bullets,
        recommendation=_clean(data.get("recommendation", ""), limit=400),
        provider=provider,
        model=model,
    )


def template_narrative(result: AnalysisResult) -> Narrative:
    """Deterministic narrative built from the computed numbers."""
    movers = result.top_movers(limit=3)
    headline_total = movers[0] if movers else None
    top_insight = result.headline_insight

    if headline_total is None:
        headline = "No metrics were available for this period."
    else:
        current = format_value(
            headline_total.current, headline_total.unit, precision=headline_total.precision
        )
        if headline_total.delta_pct is None:
            headline = (
                f"{headline_total.label} reached {current} "
                "with no comparable figure in the previous period."
            )
        else:
            verb = {"up": "rose", "down": "fell"}.get(headline_total.direction, "held steady")
            headline = (
                f"{headline_total.label} {verb} "
                f"{format_delta_pct(headline_total.delta_pct)} to {current} "
                f"over {result.period.describe()}."
            )

    bullets = [describe_total(total) for total in movers]
    for attribution in result.drivers[:2]:
        if attribution.drivers:
            leader = attribution.drivers[0]
            bullets.append(
                f"{attribution.label}: {leader.segment_label} accounts for "
                f"{abs(leader.contribution_pct or 0):.0f}% of the movement."
            )
    if not bullets and top_insight is not None:
        bullets.append(describe_insight(top_insight))
    if not bullets:
        bullets.append("No metric moved materially between the two periods.")

    if top_insight is not None and top_insight.segment:
        recommendation = (
            f"Review {top_insight.segment_label}: it drives the largest movement in "
            f"{top_insight.label}. Confirm the change is intentional before it compounds."
        )
    elif headline_total is not None and headline_total.sentiment == "negative":
        recommendation = (
            f"Investigate the decline in {headline_total.label} before the next reporting period."
        )
    else:
        recommendation = "Keep the current reporting cadence; no metric requires intervention."

    return Narrative(
        title=result.spec_title,
        headline=headline,
        bullets=bullets[:MAX_BULLETS],
        recommendation=recommendation,
        provider="template",
    )


def generate_narrative(
    result: AnalysisResult,
    provider: NarrativeProvider | None = None,
) -> Narrative:
    """Narrate ``result``, falling back to the template writer on any problem."""
    if provider is None:
        return template_narrative(result)

    try:
        completion = provider.complete(build_prompt(result))
        narrative = narrative_from_response(
            completion.text,
            provider=completion.provider,
            model=completion.model,
            fallback_title=result.spec_title,
        )
    except Exception as error:
        logger.warning(
            "narrative provider failed; using the template writer",
            extra={"provider": getattr(provider, "name", "unknown"), "error": str(error)},
        )
        return template_narrative(result)

    if not narrative.bullets:
        narrative = narrative.model_copy(update={"bullets": template_narrative(result).bullets})
    return narrative
