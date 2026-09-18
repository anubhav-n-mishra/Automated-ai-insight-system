"""Shared fixtures.

Everything is built per test from explicit arguments — no environment mutation,
no module-level singletons — so the suite runs in any order and in parallel.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from insight_engine.core.config import Settings
from insight_engine.domain.spec import AnalysisSpec, spec_from_mapping
from insight_engine.llm.base import CompletionRequest, CompletionResult
from insight_engine.service import ReportService
from insight_engine.sessions import FileSessionStore
from insight_engine.storage import LocalArtifactStore

FIXTURE_DIR = Path(__file__).parent / "fixtures"

CLICKS_CSV = textwrap.dedent(
    """\
    date,campaign,geo,impressions,clicks,spend
    2025-01-01,alpha,US,1000,50,100.0
    2025-01-01,alpha,UK,800,32,64.0
    2025-01-01,beta,US,2000,60,180.0
    2025-01-02,alpha,US,1100,55,110.0
    2025-01-02,beta,US,2100,63,189.0
    2025-01-02,beta,UK,900,27,81.0
    2025-01-08,alpha,US,1500,90,150.0
    2025-01-08,alpha,UK,850,40,68.0
    2025-01-08,beta,US,1900,50,171.0
    2025-01-09,alpha,US,1600,96,160.0
    2025-01-09,beta,US,2000,52,180.0
    2025-01-09,gamma,US,300,30,45.0
    """
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    (directory / "clicks.csv").write_text(CLICKS_CSV, encoding="utf-8")
    return directory


@pytest.fixture
def spec_mapping() -> dict[str, Any]:
    """A spec exercising dimensions, several units and two derived metrics."""
    return {
        "dataset": {
            "primary_source": "clicks",
            "sources": {
                "clicks": {
                    "type": "csv",
                    "path": "clicks.csv",
                    "date_column": "date",
                    "dimensions": ["campaign", "geo"],
                    "metrics": [
                        {"name": "impressions"},
                        {"name": "clicks"},
                        {"name": "spend", "unit": "currency"},
                    ],
                }
            },
        },
        "derived_metrics": [
            {"name": "ctr", "expression": "clicks / impressions", "unit": "percent"},
            {
                "name": "cpc",
                "expression": "spend / clicks",
                "unit": "currency",
                "higher_is_better": False,
            },
        ],
        "report": {
            "title": "Test report",
            "date_column": "date",
            "comparison": {
                "current_start": "2025-01-08",
                "current_end": "2025-01-14",
                "previous_start": "2025-01-01",
                "previous_end": "2025-01-07",
            },
            "dimensions": ["campaign", "geo"],
            "kpi_priority": ["spend", "clicks", "ctr", "cpc", "impressions"],
        },
    }


@pytest.fixture
def spec(spec_mapping: dict[str, Any]) -> AnalysisSpec:
    return spec_from_mapping(spec_mapping)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="development",
        data_dir=tmp_path / "var",
        public_base_url="https://reports.test",
        llm_provider="none",
        rate_limit_requests=0,
        worker_threads=2,
        api_keys=[],
    )


@pytest.fixture
def service(settings: Settings) -> ReportService:
    settings.ensure_directories()
    return ReportService(
        settings=settings,
        artifacts=LocalArtifactStore(settings.reports_dir),
        sessions=FileSessionStore(settings.sessions_dir),
        provider=None,
    )


class StubProvider:
    """A narrative provider that returns canned text without network access."""

    name = "stub"
    model = "stub-1"

    def __init__(self, text: str = "", *, fail: bool = False) -> None:
        self.text = text or (
            '{"title": "Stub title", "headline": "Spend rose sharply.", '
            '"bullets": ["One", "Two", "Three"], "recommendation": "Do the thing."}'
        )
        self.fail = fail
        self.calls: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("provider exploded")
        return CompletionResult(text=self.text, provider=self.name, model=self.model)


@pytest.fixture
def stub_provider() -> StubProvider:
    return StubProvider()


@pytest.fixture
def stub_provider_class() -> type[StubProvider]:
    """The class itself, for tests that need a non-default constructor call.

    A test module reaching for ``from tests.conftest import StubProvider``
    only works when the repository root happens to be on ``sys.path`` — true
    under ``python -m pytest`` (which prepends the current directory), false
    under the ``pytest`` console script CI invokes, where it fails with
    ``ModuleNotFoundError: No module named 'tests'``. Fixture injection
    sidesteps the question entirely: it works the same way regardless of how
    pytest was launched.
    """
    return StubProvider


@pytest.fixture
def client(settings: Settings, data_dir: Path) -> Iterator[Any]:
    """A TestClient over a fully wired app with isolated storage."""
    from fastapi.testclient import TestClient

    from insight_engine.api.app import create_app

    app = create_app(settings=settings, provider=None, configure_logs=False)
    with TestClient(app) as test_client:
        yield test_client
