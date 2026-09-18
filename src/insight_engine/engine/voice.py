"""Audio briefing.

Two paths, and the fallback is deliberate rather than apologetic:

``murf``
    Server-rendered MP3 when an API key is configured.
``browser``
    A structured script the page speaks with the Web Speech API. No key, no
    egress, no per-report cost, and it works offline.

Either way the script is built from the same computed figures as the deck, so
what a listener hears matches what a reader sees. The previous implementation
read an ``insight["change"]`` key the engine never wrote, so every briefing
announced that every metric had changed by 0.0 percent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from insight_engine.core.logging import get_logger
from insight_engine.domain.results import AnalysisResult
from insight_engine.engine.formatting import spoken_change, spoken_number

logger = get_logger("engine.voice")

MURF_ENDPOINT = "https://api.murf.ai/v1/speech/generate"
MAX_SPOKEN_CHARS = 2400

SegmentKind = Literal["opening", "summary", "finding", "driver", "recommendation", "closing"]


@dataclass(frozen=True)
class BriefingSegment:
    """One spoken beat, with the pause that should follow it."""

    kind: SegmentKind
    text: str
    pause_ms: int = 400

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text, "pause_ms": self.pause_ms}


@dataclass
class Briefing:
    """A complete briefing: the script, and audio if it was rendered."""

    segments: list[BriefingSegment] = field(default_factory=list)
    audio_url: str | None = None
    audio_kind: Literal["murf", "browser"] = "browser"
    voice_id: str | None = None

    @property
    def full_text(self) -> str:
        return " ".join(segment.text for segment in self.segments)

    def as_dict(self) -> dict[str, Any]:
        return {
            "audio_kind": self.audio_kind,
            "audio_url": self.audio_url,
            "voice_id": self.voice_id,
            "segments": [segment.as_dict() for segment in self.segments],
            "full_text": self.full_text,
            "estimated_seconds": round(len(self.full_text.split()) / 2.6, 1),
        }


def build_script(result: AnalysisResult) -> list[BriefingSegment]:
    """Compose the spoken script from the computed result."""
    narrative = result.narrative
    title = narrative.title if narrative else result.spec_title

    segments: list[BriefingSegment] = [
        BriefingSegment("opening", f"Here is your briefing on {title}.", 600),
        BriefingSegment(
            "summary",
            f"This compares {_spoken_period(result)}.",
            600,
        ),
    ]

    if narrative and narrative.headline:
        segments.append(BriefingSegment("summary", speakable(narrative.headline), 700))

    movers = result.top_movers(limit=3)
    if movers:
        segments.append(BriefingSegment("finding", "Taking the headline metrics in turn.", 400))
        for total in movers:
            segments.append(
                BriefingSegment(
                    "finding",
                    f"{total.label} {spoken_change(total.delta_pct, total.direction)}, "
                    f"reaching {spoken_number(total.current, total.unit)}.",
                    500,
                )
            )

    for attribution in result.drivers[:2]:
        if not attribution.drivers:
            continue
        leader = attribution.drivers[0]
        segments.append(
            BriefingSegment(
                "driver",
                f"Most of the movement in {attribution.label} came from "
                f"{_spoken_segment(leader.segment_label)}, which accounts for "
                f"{abs(leader.contribution_pct or 0):.0f} percent of the change.",
                600,
            )
        )

    if narrative and narrative.recommendation:
        segments.append(
            BriefingSegment(
                "recommendation",
                f"Recommended next step. {speakable(narrative.recommendation)}",
                700,
            )
        )

    if result.warnings:
        segments.append(
            BriefingSegment("summary", f"One caveat. {speakable(result.warnings[0])}", 500)
        )

    segments.append(
        BriefingSegment(
            "closing",
            "That is the end of the briefing. Open the dashboard for the full breakdown.",
            0,
        )
    )
    return _truncate(segments)


_ISO_RANGE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\.\.(\d{4})-(\d{2})-(\d{2})")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_SIGNED_PCT = re.compile(r"([+-])\s*(\d+(?:\.\d+)?)\s*%")
_BARE_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def speakable(text: str) -> str:
    """Rewrite written shorthand into words a synthesiser reads correctly.

    Prose written for the eye ("+40.2% over 2025-11-24..2025-11-30") is read
    aloud as "plus forty point two percent sign over twenty twenty five dash..."
    by every engine tested, so the shorthand is expanded before synthesis.
    """
    result = _ISO_RANGE.sub(
        lambda m: (
            f"{_spoken_date(m.group(1), m.group(2), m.group(3))} to "
            f"{_spoken_date(m.group(4), m.group(5), m.group(6))}"
        ),
        text,
    )
    result = _ISO_DATE.sub(lambda m: _spoken_date(m.group(1), m.group(2), m.group(3)), result)
    result = _SIGNED_PCT.sub(_expand_signed_percent, result)
    result = _BARE_PCT.sub(lambda m: f"{m.group(1)} percent", result)
    result = result.replace(" vs ", " compared with ")
    result = result.replace("_", " ")
    return re.sub(r"\s+", " ", result).strip()


#: Verbs that already carry the direction, so "rose up 40 percent" is avoided.
_DIRECTIONAL_VERBS = frozenset(
    {
        "rose",
        "fell",
        "increased",
        "decreased",
        "grew",
        "dropped",
        "climbed",
        "declined",
        "improved",
        "worsened",
        "gained",
        "lost",
        "up",
        "down",
        "by",
    }
)


def _expand_signed_percent(match: re.Match[str]) -> str:
    preceding = match.string[: match.start()].rstrip().rsplit(" ", 1)
    previous_word = preceding[-1].strip(".,:;").lower() if preceding else ""
    magnitude = f"{match.group(2)} percent"
    if previous_word in _DIRECTIONAL_VERBS:
        return f"by {magnitude}"
    return f"{'up' if match.group(1) == '+' else 'down'} {magnitude}"


_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _spoken_date(year: str, month: str, day: str) -> str:
    try:
        name = _MONTHS[int(month) - 1]
    except (ValueError, IndexError):
        return f"{year}-{month}-{day}"
    return f"{int(day)} {name} {year}"


def _spoken_period(result: AnalysisResult) -> str:
    period = result.period
    return (
        f"the {period.current_days} days ending {speakable(period.current_end)} "
        f"against the {period.previous_days} days ending {speakable(period.previous_end)}"
    )


def _spoken_segment(label: str) -> str:
    """Turn ``campaign: Performance_Max, geo: US`` into something readable aloud."""
    parts = [piece.split(":", 1)[-1].strip() for piece in label.split(",")]
    cleaned = [part.replace("_", " ").replace("-", " ") for part in parts if part]
    return " in ".join(cleaned) if cleaned else label


def _truncate(segments: list[BriefingSegment]) -> list[BriefingSegment]:
    """Keep the script inside the synthesis budget, dropping from the middle."""
    total = sum(len(segment.text) for segment in segments)
    if total <= MAX_SPOKEN_CHARS:
        return segments

    kept: list[BriefingSegment] = []
    budget = MAX_SPOKEN_CHARS
    for segment in segments:
        if (
            segment.kind in {"opening", "recommendation", "closing"}
            or budget - len(segment.text) > 0
        ):
            kept.append(segment)
            budget -= len(segment.text)
    return kept


def render_murf_audio(
    text: str,
    *,
    api_key: str,
    voice_id: str = "en-US-natalie",
    timeout_seconds: float = 60.0,
    client: httpx.Client | None = None,
) -> bytes | None:
    """Synthesise ``text`` with Murf. Returns ``None`` on any failure.

    Audio is an enhancement, so a provider outage must never fail a report.
    """
    payload = {
        "voiceId": voice_id,
        "style": "Conversational",
        "text": text[:MAX_SPOKEN_CHARS],
        "format": "MP3",
        "sampleRate": 24000,
        "channelType": "MONO",
        "modelVersion": "GEN2",
    }
    headers = {"api-key": api_key, "content-type": "application/json"}

    owns_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0))
    try:
        response = http.post(MURF_ENDPOINT, json=payload, headers=headers)
        if response.status_code >= 400:
            logger.warning("murf synthesis rejected", extra={"status": response.status_code})
            return None

        audio_url = response.json().get("audioFile")
        if not audio_url:
            logger.warning("murf response contained no audio url")
            return None

        audio = http.get(audio_url, timeout=timeout_seconds)
        if audio.status_code >= 400:
            logger.warning("murf audio download failed", extra={"status": audio.status_code})
            return None
        return audio.content
    except httpx.HTTPError as error:
        logger.warning("murf request failed", extra={"error": str(error)})
        return None
    finally:
        if owns_client:
            http.close()
