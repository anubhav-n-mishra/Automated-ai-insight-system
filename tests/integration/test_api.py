"""HTTP surface: the contract clients depend on."""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from insight_engine.api.app import create_app
from insight_engine.core.config import Settings

pytestmark = pytest.mark.integration

CSV = (
    "date,campaign,geo,impressions,clicks,spend\n"
    "2025-01-01,alpha,US,1000,50,100\n"
    "2025-01-02,alpha,UK,900,40,90\n"
    "2025-01-02,beta,US,2000,60,180\n"
    "2025-01-08,alpha,US,1500,90,150\n"
    "2025-01-09,alpha,UK,950,45,95\n"
    "2025-01-09,beta,US,1900,50,171\n"
    "2025-01-10,gamma,US,300,30,45\n"
)


def upload(client: TestClient, content: str = CSV, name: str = "clicks.csv") -> dict[str, Any]:
    response = client.post(
        "/api/v1/datasets", files={"file": (name, io.BytesIO(content.encode()), "text/csv")}
    )
    assert response.status_code == 201, response.text
    return response.json()


def wait_for_job(client: TestClient, job_id: str, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")


class TestOperations:
    def test_health_is_unauthenticated(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_readiness_reports_its_checks(self, client: TestClient) -> None:
        body = client.get("/health/ready").json()
        assert body["status"] == "ready"
        assert body["checks"]["storage_writable"]

    def test_metrics_are_exposed_in_prometheus_format(self, client: TestClient) -> None:
        client.get("/health")
        body = client.get("/metrics").text
        assert "insight_engine_http_requests_total" in body
        assert "# TYPE" in body

    def test_openapi_document_is_served(self, client: TestClient) -> None:
        document = client.get("/openapi.json").json()
        assert document["info"]["title"] == "Insight Engine"
        assert "/api/v1/reports" in document["paths"]

    def test_every_response_carries_a_correlation_id(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.headers["X-Request-ID"]

    def test_security_headers_are_applied(self, client: TestClient) -> None:
        headers = client.get("/health").headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert "default-src 'self'" in headers["Content-Security-Policy"]
        # No 'unsafe-inline': the UI ships its CSS and JS as separate files, so
        # injected markup cannot execute.
        assert "unsafe-inline" not in headers["Content-Security-Policy"]


class TestDatasetProfiling:
    def test_profiles_locally_and_suggests_a_range_inside_the_data(
        self, client: TestClient
    ) -> None:
        profile = upload(client)
        assert profile["row_count"] == 7
        assert profile["date_column"] == "date"
        # The old UI defaulted to "the last 14 days from today", which for any
        # historical extract selected two empty windows.
        assert profile["date_range"]["start"] == "2025-01-01"
        assert profile["suggested_comparison"]["current_end"] <= profile["date_range"]["end"]
        assert set(profile["suggested_dimensions"]) >= {"campaign", "geo"}
        assert set(profile["suggested_metrics"]) >= {"impressions", "clicks", "spend"}

    def test_rejects_an_empty_file(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/datasets", files={"file": ("e.csv", io.BytesIO(b"   "), "text/csv")}
        )
        assert response.status_code in {400, 422}

    def test_rejects_a_spreadsheet_renamed_to_csv(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/datasets",
            files={"file": ("book.csv", io.BytesIO(b"PK\x03\x04\x00garbage"), "text/csv")},
        )
        assert response.status_code == 400
        assert "delimited text" in response.json()["error"]["message"]

    def test_detects_semicolon_delimiters(self, client: TestClient) -> None:
        content = "date;geo;amount\n2025-01-01;US;10\n2025-01-02;UK;20\n"
        profile = upload(client, content, "euro.csv")
        assert profile["row_count"] == 2
        assert profile["date_column"] == "date"


class TestReportLifecycle:
    def test_full_run_produces_a_deck_and_a_dashboard(self, client: TestClient) -> None:
        profile = upload(client)
        accepted = client.post(
            "/api/v1/reports",
            json={
                "upload_id": profile["upload_id"],
                "title": "Weekly",
                "date_column": "date",
                "dimensions": ["campaign", "geo"],
                "metrics": [
                    {"name": "impressions"},
                    {"name": "clicks"},
                    {"name": "spend", "unit": "currency"},
                ],
                "derived_metrics": [
                    {"name": "ctr", "expression": "clicks / impressions", "unit": "percent"}
                ],
                "current_start": "2025-01-08",
                "current_end": "2025-01-14",
                "previous_start": "2025-01-01",
                "previous_end": "2025-01-07",
            },
        )
        assert accepted.status_code == 202, accepted.text
        assert accepted.headers["Location"].startswith("/api/v1/jobs/")

        job = wait_for_job(client, accepted.json()["job_id"])
        assert job["status"] == "succeeded", job
        assert job["progress"] == 1.0

        result = job["result"]
        analysis = result["analysis"]

        # The comparison chart used to read keys the engine never produced, so
        # every deck printed "Insufficient data".
        assert len(analysis["totals"]) == 4
        assert analysis["insights"]
        assert analysis["narrative"]["provider"] == "template"

        deck = client.get(result["report"]["download_url"])
        assert deck.status_code == 200
        assert deck.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert deck.content[:2] == b"PK"

        dashboard_url = result["dashboard_url"]
        assert dashboard_url.startswith("https://reports.test/d/")

    def test_a_job_is_pollable_and_reports_real_stages(self, client: TestClient) -> None:
        profile = upload(client)
        accepted = client.post(
            "/api/v1/reports",
            json={
                "upload_id": profile["upload_id"],
                "date_column": "date",
                "dimensions": ["geo"],
                "metrics": [{"name": "clicks"}],
                "current_start": "2025-01-08",
                "current_end": "2025-01-14",
                "previous_start": "2025-01-01",
                "previous_end": "2025-01-07",
            },
        )
        job_id = accepted.json()["job_id"]
        status = client.get(f"/api/v1/jobs/{job_id}")
        assert status.headers["Cache-Control"] == "no-store"
        assert wait_for_job(client, job_id)["status"] == "succeeded"

    def test_unknown_job_is_not_found(self, client: TestClient) -> None:
        assert client.get("/api/v1/jobs/deadbeef").status_code == 404

    def test_expired_upload_reference_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/reports",
            json={
                "upload_id": "does-not-exist",
                "date_column": "date",
                "metrics": [{"name": "clicks"}],
                "current_start": "2025-01-08",
                "current_end": "2025-01-14",
                "previous_start": "2025-01-01",
                "previous_end": "2025-01-07",
            },
        )
        assert response.status_code == 404

    def test_overlapping_periods_are_rejected_before_any_work(self, client: TestClient) -> None:
        profile = upload(client)
        response = client.post(
            "/api/v1/reports",
            json={
                "upload_id": profile["upload_id"],
                "date_column": "date",
                "metrics": [{"name": "clicks"}],
                "current_start": "2025-01-05",
                "current_end": "2025-01-12",
                "previous_start": "2025-01-08",
                "previous_end": "2025-01-14",
            },
        )
        assert response.status_code == 400
        assert "overlap" in response.json()["error"]["message"]

    def test_a_column_cannot_be_both_dimension_and_metric(self, client: TestClient) -> None:
        profile = upload(client)
        response = client.post(
            "/api/v1/reports",
            json={
                "upload_id": profile["upload_id"],
                "date_column": "date",
                "dimensions": ["clicks"],
                "metrics": [{"name": "clicks"}],
                "current_start": "2025-01-08",
                "current_end": "2025-01-14",
                "previous_start": "2025-01-01",
                "previous_end": "2025-01-07",
            },
        )
        assert response.status_code == 400


class TestDashboardAccess:
    @pytest.fixture
    def session_link(self, client: TestClient) -> tuple[str, str]:
        profile = upload(client)
        accepted = client.post(
            "/api/v1/reports",
            json={
                "upload_id": profile["upload_id"],
                "date_column": "date",
                "dimensions": ["geo"],
                "metrics": [{"name": "clicks"}],
                "current_start": "2025-01-08",
                "current_end": "2025-01-14",
                "previous_start": "2025-01-01",
                "previous_end": "2025-01-07",
            },
        )
        job = wait_for_job(client, accepted.json()["job_id"])
        result = job["result"]
        token = result["dashboard_url"].split("token=")[1]
        return result["session_id"], token

    def test_valid_token_returns_the_analysis(
        self, client: TestClient, session_link: tuple[str, str]
    ) -> None:
        session_id, token = session_link
        response = client.get(f"/api/v1/dashboards/{session_id}", params={"token": token})
        assert response.status_code == 200
        body = response.json()
        assert body["analysis"]["totals"]
        assert body["briefing"]["segments"]
        assert response.headers["Cache-Control"] == "private, no-store"

    def test_missing_token_is_refused(
        self, client: TestClient, session_link: tuple[str, str]
    ) -> None:
        """The auth bypass.

        ``token: str = None`` plus ``if token and ...`` meant that omitting the
        parameter skipped the check, so any session id granted full access.
        """
        session_id, _ = session_link
        assert client.get(f"/api/v1/dashboards/{session_id}").status_code == 422

    def test_wrong_token_is_refused(
        self, client: TestClient, session_link: tuple[str, str]
    ) -> None:
        session_id, _ = session_link
        response = client.get(f"/api/v1/dashboards/{session_id}", params={"token": "x" * 40})
        assert response.status_code == 404

    def test_unknown_session_and_bad_token_are_indistinguishable(
        self, client: TestClient, session_link: tuple[str, str]
    ) -> None:
        """Both must be 404, or the endpoint enumerates which sessions exist."""
        session_id, _ = session_link
        wrong_token = client.get(f"/api/v1/dashboards/{session_id}", params={"token": "y" * 40})
        unknown = client.get("/api/v1/dashboards/aaaaaaaaaaaa", params={"token": "y" * 40})
        assert wrong_token.status_code == unknown.status_code == 404
        assert wrong_token.json()["error"]["code"] == unknown.json()["error"]["code"]


class TestArtifactIsolation:
    def test_session_files_are_not_reachable_over_http(self, client: TestClient) -> None:
        """The token leak.

        The previous build mounted the working directory at ``/tmp``, exposing
        ``tmp/sessions/*.json`` and every live access token with it.
        """
        for path in (
            "/tmp/sessions/x.json",
            "/static/reports/x.pptx",
            "/api/v1/artifacts/../../etc/passwd",
            "/api/v1/artifacts/..%2F..%2Fetc%2Fpasswd",
        ):
            assert client.get(path).status_code in {307, 400, 404, 405}

    def test_unknown_artifact_is_not_found(self, client: TestClient) -> None:
        assert client.get("/api/v1/artifacts/deadbeef.pptx").status_code == 404


class TestErrorEnvelope:
    def test_validation_errors_name_the_fields(self, client: TestClient) -> None:
        response = client.post("/api/v1/reports", json={"upload_id": "x"})
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "validation_error"
        assert body["error"]["context"]["fields"]

    def test_errors_carry_the_request_id(self, client: TestClient) -> None:
        body = client.get("/api/v1/jobs/nope").json()
        assert body["request_id"]


class TestAuthenticatedDeployment:
    @pytest.fixture
    def secured_client(self, tmp_path: Path):
        settings = Settings(
            environment="development",
            data_dir=tmp_path / "var",
            public_base_url="https://reports.test",
            llm_provider="none",
            api_keys=[SecretStr("top-secret")],
            rate_limit_requests=0,
        )
        with TestClient(create_app(settings=settings, configure_logs=False)) as client:
            yield client

    def test_health_stays_open(self, secured_client: TestClient) -> None:
        assert secured_client.get("/health").status_code == 200

    def test_protected_routes_require_the_key(self, secured_client: TestClient) -> None:
        response = secured_client.post(
            "/api/v1/datasets", files={"file": ("a.csv", io.BytesIO(CSV.encode()), "text/csv")}
        )
        assert response.status_code == 401

    def test_a_valid_key_is_accepted(self, secured_client: TestClient) -> None:
        response = secured_client.post(
            "/api/v1/datasets",
            files={"file": ("a.csv", io.BytesIO(CSV.encode()), "text/csv")},
            headers={"X-API-Key": "top-secret"},
        )
        assert response.status_code == 201


class TestRateLimiting:
    def test_the_limit_is_enforced_with_a_retry_after(self, tmp_path: Path) -> None:
        settings = Settings(
            environment="development",
            data_dir=tmp_path / "var",
            public_base_url="https://reports.test",
            llm_provider="none",
            rate_limit_requests=2,
            rate_limit_window_seconds=60,
        )
        with TestClient(create_app(settings=settings, configure_logs=False)) as client:
            files = {"file": ("a.csv", io.BytesIO(CSV.encode()), "text/csv")}
            for _ in range(2):
                client.post(
                    "/api/v1/datasets",
                    files={"file": ("a.csv", io.BytesIO(CSV.encode()), "text/csv")},
                )
            blocked = client.post("/api/v1/datasets", files=files)
            assert blocked.status_code == 429
            assert blocked.headers["Retry-After"]


class TestStaticUi:
    def test_the_builder_page_is_served(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert "Insight Engine" in response.text

    def test_the_dashboard_shell_is_served(self, client: TestClient) -> None:
        assert client.get("/d/anything").status_code == 200

    def test_assets_are_served(self, client: TestClient) -> None:
        for asset in ("app.css", "ui.js", "app.js", "dashboard.js", "favicon.svg"):
            assert client.get(f"/assets/{asset}").status_code == 200, asset
