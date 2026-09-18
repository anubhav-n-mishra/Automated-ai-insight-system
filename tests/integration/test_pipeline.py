"""End-to-end pipeline behaviour on real files."""

from __future__ import annotations

from pathlib import Path

import pytest

from insight_engine.connectors import SourcePolicy
from insight_engine.core.errors import DataError, SourceError
from insight_engine.domain.spec import AnalysisSpec, spec_from_mapping
from insight_engine.engine.narrative import template_narrative
from insight_engine.engine.pipeline import run_analysis
from insight_engine.engine.qrcode_gen import build_qr_png
from insight_engine.engine.report import build_deck
from insight_engine.engine.voice import build_script, speakable

pytestmark = pytest.mark.integration


def policy(data_dir: Path) -> SourcePolicy:
    return SourcePolicy(base_path=data_dir)


class TestPipeline:
    def test_produces_totals_insights_and_drivers(self, spec: AnalysisSpec, data_dir: Path) -> None:
        result = run_analysis(spec, policy=policy(data_dir))

        assert {total.metric for total in result.totals} == {
            "impressions",
            "clicks",
            "spend",
            "ctr",
            "cpc",
        }
        assert result.insights
        assert result.drivers
        assert result.row_count == 12
        assert result.duration_ms >= 0

    def test_ratios_are_computed_from_aggregated_inputs(
        self, spec: AnalysisSpec, data_dir: Path
    ) -> None:
        result = run_analysis(spec, policy=policy(data_dir))
        ctr = result.total_for("ctr")
        assert ctr is not None
        # Current period: 90+40+50+96+52+30 = 358 clicks over
        # 1500+850+1900+1600+2000+300 = 8150 impressions.
        assert ctr.current == pytest.approx(358 / 8150)

    def test_a_segment_appearing_only_now_is_flagged(
        self, spec: AnalysisSpec, data_dir: Path
    ) -> None:
        result = run_analysis(spec, policy=policy(data_dir))
        gamma = [i for i in result.insights if i.segment.get("campaign") == "gamma"]
        assert gamma
        assert all(insight.is_new_segment for insight in gamma)
        assert all(insight.delta_pct is None for insight in gamma)

    def test_drivers_state_how_much_they_explain(self, spec: AnalysisSpec, data_dir: Path) -> None:
        result = run_analysis(spec, policy=policy(data_dir))
        attribution = result.drivers[0]
        assert 0 < abs(attribution.explained_pct) <= 200
        assert attribution.segment_count >= len(attribution.drivers)

    def test_progress_is_reported_monotonically(self, spec: AnalysisSpec, data_dir: Path) -> None:
        seen: list[float] = []
        run_analysis(spec, policy=policy(data_dir), on_progress=lambda _, f: seen.append(f))
        assert seen == sorted(seen)
        assert seen[-1] == 1.0

    def test_a_narrator_failure_is_recorded_as_a_warning_not_an_exception(
        self, spec: AnalysisSpec, data_dir: Path
    ) -> None:
        def broken(result):
            raise RuntimeError("no")

        result = run_analysis(spec, policy=policy(data_dir), narrator=broken)
        assert result.narrative is None
        assert any("Narrative generation failed" in w for w in result.warnings)

    def test_a_missing_file_names_the_file_not_the_server_path(
        self, spec: AnalysisSpec, tmp_path: Path
    ) -> None:
        with pytest.raises(SourceError) as error:
            run_analysis(spec, policy=SourcePolicy(base_path=tmp_path))
        assert "clicks.csv" in error.value.message
        assert str(tmp_path) not in error.value.message

    def test_a_date_range_outside_the_data_explains_itself(
        self, spec_mapping: dict, data_dir: Path
    ) -> None:
        spec_mapping["report"]["comparison"] = {
            "current_start": "2030-01-08",
            "current_end": "2030-01-14",
            "previous_start": "2030-01-01",
            "previous_end": "2030-01-07",
        }
        with pytest.raises(DataError) as error:
            run_analysis(spec_from_mapping(spec_mapping), policy=policy(data_dir))
        assert "2025-01-01" in str(error.value.context)


class TestRendering:
    def test_a_deck_is_produced_with_every_section(
        self, spec: AnalysisSpec, data_dir: Path, tmp_path: Path
    ) -> None:
        result = run_analysis(spec, policy=policy(data_dir), narrator=template_narrative)
        path = build_deck(
            result,
            tmp_path / "out",
            dashboard_url="https://reports.test/d/abc?token=xyz",
            qr_png=build_qr_png("https://reports.test/d/abc?token=xyz"),
        )
        assert path.is_file()
        assert path.read_bytes()[:2] == b"PK"
        assert path.stat().st_size > 20_000

    def test_a_deck_renders_without_a_narrative(
        self, spec: AnalysisSpec, data_dir: Path, tmp_path: Path
    ) -> None:
        result = run_analysis(spec, policy=policy(data_dir))
        assert build_deck(result, tmp_path / "out").is_file()

    def test_a_qr_code_is_a_png(self) -> None:
        png = build_qr_png("https://reports.test/d/abc?token=xyz")
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_an_overlong_url_is_refused(self) -> None:
        from insight_engine.core.errors import RenderError

        with pytest.raises(RenderError):
            build_qr_png("https://x.test/" + "a" * 4000)


class TestBriefing:
    def test_the_script_quotes_the_computed_figures(
        self, spec: AnalysisSpec, data_dir: Path
    ) -> None:
        result = run_analysis(spec, policy=policy(data_dir), narrator=template_narrative)
        script = build_script(result)
        text = " ".join(segment.text for segment in script)

        assert script[0].kind == "opening"
        assert script[-1].kind == "closing"
        # The previous implementation read a key the engine never wrote, so
        # every briefing said every metric had moved by 0.0 percent.
        assert "0.0 percent" not in text
        assert "percent" in text

    @pytest.mark.parametrize(
        ("written", "expected"),
        [
            ("Spend is +12.5% this week.", "up 12.5 percent"),
            ("Clicks rose +40.2%.", "rose by 40.2 percent"),
            ("Period 2025-01-08..2025-01-14", "8 January 2025 to 14 January 2025"),
            ("campaign_name", "campaign name"),
        ],
    )
    def test_written_shorthand_is_expanded_for_speech(self, written: str, expected: str) -> None:
        assert expected in speakable(written)
