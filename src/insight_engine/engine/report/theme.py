"""Deck theme.

Colours are a data structure rather than constants scattered through the
renderer, so an organisation can ship its own palette without forking the
layout code. The defaults are checked for contrast: every text/background pair
used below clears WCAG AA at the size it is rendered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from pptx.dml.color import RGBColor


def _rgb(value: str) -> RGBColor:
    # python-pptx ships no type information for this constructor.
    return cast(RGBColor, RGBColor.from_string(value.lstrip("#").upper()))


@dataclass(frozen=True)
class Theme:
    """Palette, fonts and the categorical series colours for charts."""

    name: str = "default"

    primary: str = "#10367D"
    primary_dark: str = "#0A2255"
    accent: str = "#B35C00"
    surface: str = "#FFFFFF"
    surface_muted: str = "#F2F4F8"
    text: str = "#1B1F27"
    text_muted: str = "#525B6B"
    text_inverse: str = "#FFFFFF"
    positive: str = "#1B6E3C"
    negative: str = "#A4232B"
    neutral: str = "#525B6B"

    heading_font: str = "Calibri"
    body_font: str = "Calibri"

    # Ordered so adjacent series stay distinguishable in greyscale and for the
    # most common forms of colour vision deficiency.
    series: tuple[str, ...] = (
        "#10367D",
        "#B35C00",
        "#1B6E3C",
        "#6B3FA0",
        "#0E7490",
        "#A4232B",
    )

    _cache: dict[str, RGBColor] = field(default_factory=dict, repr=False, compare=False)

    def color(self, hex_value: str) -> RGBColor:
        if hex_value not in self._cache:
            self._cache[hex_value] = _rgb(hex_value)
        return self._cache[hex_value]

    def sentiment_color(self, sentiment: str) -> RGBColor:
        return self.color(
            {"positive": self.positive, "negative": self.negative}.get(sentiment, self.neutral)
        )

    def series_color(self, index: int) -> RGBColor:
        return self.color(self.series[index % len(self.series)])


DEFAULT_THEME = Theme()
