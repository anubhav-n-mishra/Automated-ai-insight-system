"""Narrative generation and its fallbacks."""

from __future__ import annotations

import pytest

from insight_engine.domain.results import AnalysisResult, MetricTotals, Narrative, PeriodSummary
from insight_engine.engine import narrative as narrative_module
from insight_engine.llm.base import CompletionRequest, CompletionResult, ResponseCache


@pytest.fixture
def result() -> AnalysisResult:
    return AnalysisResult(
        spec_title="Weekly report",
        period=PeriodSummary(
            current_start="2025-01-08",
            current_end="2025-01-14",
            previous_start="2025-01-01",
            previous_end="2025-01-07",
            current_rows=100,
            previous_rows=90,
            current_days=7,
            previous_days=7,
        ),
        dimensions=["geo"],
        totals=[
            MetricTotals.build(
                metric="spend", label="Spend", unit="currency", current=1200.0, previous=1000.0
            ),
            MetricTotals.build(
                metric="clicks", label="Clicks", unit="count", current=500.0, previous=600.0
            ),
        ],
        row_count=100,
        segment_count=4,
    )


class TestTemplateNarrative:
    def test_states_the_largest_relative_movement(self, result: AnalysisResult) -> None:
        narrative = narrative_module.template_narrative(result)
        assert narrative.provider == "template"
        assert "%" in narrative.headline
        assert narrative.bullets
        assert narrative.recommendation

    def test_handles_an_undefined_baseline_without_inventing_a_percentage(self) -> None:
        result = AnalysisResult(
            spec_title="New launch",
            period=PeriodSummary(
                current_start="2025-01-08",
                current_end="2025-01-14",
                previous_start="2025-01-01",
                previous_end="2025-01-07",
                current_rows=10,
                previous_rows=0,
                current_days=7,
                previous_days=7,
            ),
            totals=[
                MetricTotals.build(
                    metric="signups", label="Signups", unit="count", current=42.0, previous=0.0
                )
            ],
        )
        narrative = narrative_module.template_narrative(result)
        assert "no comparable" in narrative.headline

    def test_survives_an_empty_result(self) -> None:
        result = AnalysisResult(
            spec_title="Nothing",
            period=PeriodSummary(
                current_start="2025-01-08",
                current_end="2025-01-14",
                previous_start="2025-01-01",
                previous_end="2025-01-07",
                current_rows=0,
                previous_rows=0,
                current_days=7,
                previous_days=7,
            ),
        )
        assert narrative_module.template_narrative(result).bullets


class TestResponseParsing:
    @pytest.mark.parametrize(
        "raw",
        [
            '{"title":"T","headline":"H","bullets":["a"],"recommendation":"R"}',
            '```json\n{"title":"T","headline":"H","bullets":["a"],"recommendation":"R"}\n```',
            'Sure!\n{"title":"T","headline":"H","bullets":["a"],"recommendation":"R"}\nHope!',
        ],
    )
    def test_tolerates_the_shapes_models_actually_emit(self, raw: str) -> None:
        parsed = narrative_module.narrative_from_response(
            raw, provider="p", model="m", fallback_title="f"
        )
        assert parsed.headline == "H"

    def test_rejects_a_response_with_no_headline(self) -> None:
        with pytest.raises(ValueError, match="headline"):
            narrative_module.narrative_from_response(
                '{"title":"T","bullets":[]}', provider="p", model="m", fallback_title="f"
            )

    def test_rejects_non_json(self) -> None:
        with pytest.raises(ValueError):
            narrative_module.narrative_from_response(
                "no json here", provider="p", model="m", fallback_title="f"
            )

    def test_truncates_runaway_fields(self) -> None:
        raw = '{"title":"T","headline":"' + "x" * 5000 + '","bullets":[],"recommendation":""}'
        parsed = narrative_module.narrative_from_response(
            raw, provider="p", model="m", fallback_title="f"
        )
        assert len(parsed.headline) <= 300

    def test_caps_the_bullet_count(self) -> None:
        bullets = ",".join(f'"b{i}"' for i in range(30))
        raw = '{"title":"T","headline":"H","bullets":[' + bullets + '],"recommendation":"R"}'
        parsed = narrative_module.narrative_from_response(
            raw, provider="p", model="m", fallback_title="f"
        )
        assert len(parsed.bullets) <= narrative_module.MAX_BULLETS


class TestProviderIntegration:
    def test_a_provider_response_is_used(self, result: AnalysisResult, stub_provider) -> None:
        narrative = narrative_module.generate_narrative(result, stub_provider)
        assert narrative.provider == "stub"
        assert stub_provider.calls

    def test_a_failing_provider_degrades_to_the_template(self, result: AnalysisResult) -> None:
        """The numbers are the product; prose is a layer over them."""
        from tests.conftest import StubProvider

        narrative = narrative_module.generate_narrative(result, StubProvider(fail=True))
        assert narrative.provider == "template"

    def test_garbage_from_a_provider_degrades_to_the_template(self, result: AnalysisResult) -> None:
        from tests.conftest import StubProvider

        narrative = narrative_module.generate_narrative(result, StubProvider(text="not json"))
        assert narrative.provider == "template"

    def test_the_prompt_carries_only_computed_figures(self, result: AnalysisResult) -> None:
        request = narrative_module.build_prompt(result)
        assert "Spend" in request.user
        assert "REPORTING PERIOD" in request.user
        assert "never invent a figure" in request.system

    def test_warnings_reach_the_prompt_as_caveats(self, result: AnalysisResult) -> None:
        with_warning = result.model_copy(update={"warnings": ["Periods are unequal."]})
        assert "CAVEATS" in narrative_module.build_prompt(with_warning).user


class TestResponseCache:
    def test_repeats_are_served_from_cache(self) -> None:
        cache = ResponseCache(max_entries=4)
        request = CompletionRequest(system="s", user="u")
        key = ResponseCache.key("p", "m", request)
        cache.put(key, CompletionResult(text="t", provider="p", model="m"))

        hit = cache.get(key)
        assert hit is not None
        assert hit.cached

    def test_eviction_is_bounded(self) -> None:
        cache = ResponseCache(max_entries=2)
        for index in range(5):
            request = CompletionRequest(system="s", user=f"u{index}")
            cache.put(
                ResponseCache.key("p", "m", request),
                CompletionResult(text="t", provider="p", model="m"),
            )
        first = CompletionRequest(system="s", user="u0")
        assert cache.get(ResponseCache.key("p", "m", first)) is None

    def test_a_zero_size_cache_stores_nothing(self) -> None:
        cache = ResponseCache(max_entries=0)
        request = CompletionRequest(system="s", user="u")
        key = ResponseCache.key("p", "m", request)
        cache.put(key, CompletionResult(text="t", provider="p", model="m"))
        assert cache.get(key) is None


class TestNarrativeModel:
    def test_template_narratives_are_not_labelled_ai_generated(self) -> None:
        assert not Narrative(title="t", headline="h", provider="template").is_ai_generated
        assert Narrative(title="t", headline="h", provider="gemini").is_ai_generated
