"""Jobs, storage, sessions and the service layer."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from insight_engine.core.errors import InsightEngineError, StorageError
from insight_engine.jobs import JobManager, JobStatus
from insight_engine.sessions import DashboardSession, FileSessionStore, SessionSecret
from insight_engine.storage import LocalArtifactStore


def drain(manager: JobManager, job_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        assert job is not None
        if job.status.is_terminal:
            return job
        time.sleep(0.01)
    raise AssertionError("job never finished")


class TestJobManager:
    def test_a_job_runs_and_reports_its_result(self) -> None:
        manager = JobManager(max_workers=2)
        try:
            job = manager.submit(lambda report: (report("work", 0.5), {"ok": True})[1])
            finished = drain(manager, job.id)
            assert finished.status is JobStatus.SUCCEEDED
            assert finished.result == {"ok": True}
            assert finished.progress == 1.0
        finally:
            manager.shutdown()

    def test_a_failure_never_leaks_internal_detail(self) -> None:
        manager = JobManager(max_workers=1)
        try:

            def explode(report):
                raise RuntimeError("/srv/secret/path and a connection string")

            job = drain(manager, manager.submit(explode).id)
            assert job.status is JobStatus.FAILED
            assert "secret" not in (job.error_message or "")
            assert job.error_code == "internal_error"
        finally:
            manager.shutdown()

    def test_a_domain_error_keeps_its_code_and_message(self) -> None:
        manager = JobManager(max_workers=1)
        try:

            def fail(report):
                raise InsightEngineError("Readable problem")

            job = drain(manager, manager.submit(fail).id)
            assert job.error_message == "Readable problem"
        finally:
            manager.shutdown()

    def test_progress_never_goes_backwards(self) -> None:
        manager = JobManager(max_workers=1)
        try:

            def body(report):
                report("a", 0.8)
                report("b", 0.2)
                return {}

            job = drain(manager, manager.submit(body).id)
            assert job.progress == 1.0
        finally:
            manager.shutdown()

    def test_jobs_are_scoped_to_their_owner(self) -> None:
        manager = JobManager(max_workers=1)
        try:
            job = manager.submit(lambda report: {}, owner="key-a")
            drain(manager, job.id)
            assert manager.get(job.id, owner="key-a") is not None
            assert manager.get(job.id, owner="key-b") is None
        finally:
            manager.shutdown()

    def test_terminal_jobs_are_purged_after_retention(self) -> None:
        manager = JobManager(max_workers=1, retention_seconds=0)
        try:
            job = manager.submit(lambda report: {})
            drain(manager, job.id)
            assert manager.purge_expired() == 1
            assert manager.get(job.id) is None
        finally:
            manager.shutdown()

    def test_submitting_after_shutdown_is_refused(self) -> None:
        manager = JobManager(max_workers=1)
        manager.shutdown()
        with pytest.raises(InsightEngineError):
            manager.submit(lambda report: {})

    def test_stats_report_queue_depth(self) -> None:
        manager = JobManager(max_workers=1)
        try:
            drain(manager, manager.submit(lambda report: {}).id)
            stats = manager.stats()
            assert stats["succeeded"] == 1
            assert stats["workers"] == 1
        finally:
            manager.shutdown()


class TestLocalArtifactStore:
    def test_round_trip(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(tmp_path)
        stored = store.put(
            b"data", key="a.pptx", media_type="application/x", download_name="a.pptx"
        )
        assert stored.size_bytes == 4
        assert store.open("a.pptx") == b"data"

    def test_rejects_a_traversing_key(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(tmp_path)
        for key in ("../escape", "a/b", ".hidden", ""):
            with pytest.raises(StorageError):
                store.put(b"x", key=key, media_type="application/x", download_name="x")

    def test_expired_artifacts_are_dropped(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(tmp_path, retention_seconds=0)
        store.put(b"x", key="a.pptx", media_type="application/x", download_name="a.pptx")
        time.sleep(0.01)
        assert store.get("a.pptx") is None

    def test_purge_counts_removals(self, tmp_path: Path) -> None:
        store = LocalArtifactStore(tmp_path, retention_seconds=0)
        store.put(b"x", key="a.pptx", media_type="application/x", download_name="a.pptx")
        time.sleep(0.01)
        assert store.purge_expired() == 1

    def test_a_missing_artifact_reads_as_none(self, tmp_path: Path) -> None:
        assert LocalArtifactStore(tmp_path).open("nope.pptx") is None

    def test_writes_are_atomic(self, tmp_path: Path) -> None:
        """A reader must never observe a half-written deck."""
        store = LocalArtifactStore(tmp_path)
        store.put(b"x" * 1000, key="a.pptx", media_type="application/x", download_name="a.pptx")
        assert not list(tmp_path.glob(".*partial"))


class TestFileSessionStore:
    def test_round_trip(self, tmp_path: Path) -> None:
        store = FileSessionStore(tmp_path)
        secret = SessionSecret.mint()
        store.save(DashboardSession.create(secret, title="T", payload={"a": 1}))
        loaded = store.load(secret.session_id)
        assert loaded is not None
        assert loaded.payload == {"a": 1}

    def test_expired_sessions_are_not_returned(self, tmp_path: Path) -> None:
        store = FileSessionStore(tmp_path)
        secret = SessionSecret.mint()
        session = DashboardSession.create(secret, title="T", payload={})
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        store.save(session)
        assert store.load(secret.session_id) is None

    def test_a_corrupt_file_is_discarded_not_raised(self, tmp_path: Path) -> None:
        store = FileSessionStore(tmp_path)
        (tmp_path / "abcdefghij.json").write_text("{not json", encoding="utf-8")
        assert store.load("abcdefghij") is None

    def test_an_unsafe_session_id_never_touches_the_filesystem(self, tmp_path: Path) -> None:
        assert FileSessionStore(tmp_path).load("../../etc/passwd") is None

    def test_purge_removes_expired_records(self, tmp_path: Path) -> None:
        store = FileSessionStore(tmp_path)
        secret = SessionSecret.mint()
        session = DashboardSession.create(secret, title="T", payload={})
        session.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        store.save(session)
        assert store.purge_expired() == 1

    def test_the_cache_is_bounded(self, tmp_path: Path) -> None:
        store = FileSessionStore(tmp_path, cache_size=2)
        secrets_minted = []
        for _ in range(5):
            secret = SessionSecret.mint()
            secrets_minted.append(secret)
            store.save(DashboardSession.create(secret, title="T", payload={}))
        # Still resolvable from disk even once evicted from memory.
        assert store.load(secrets_minted[0].session_id) is not None

    def test_stored_records_are_not_world_readable(self, tmp_path: Path) -> None:
        store = FileSessionStore(tmp_path)
        secret = SessionSecret.mint()
        store.save(DashboardSession.create(secret, title="T", payload={}))
        path = tmp_path / f"{secret.session_id}.json"
        assert path.stat().st_mode & 0o077 == 0
        assert "token_hash" in json.loads(path.read_text())


class TestReportService:
    def test_generate_report_publishes_everything(self, service, spec, data_dir: Path) -> None:
        stages: list[str] = []
        outcome = service.generate_report(
            spec, base_path=data_dir, on_progress=lambda stage, _: stages.append(stage)
        )

        assert outcome.result.totals
        assert outcome.dashboard_url.startswith("https://reports.test/d/")
        assert "complete" in stages

        artifact = service.fetch_artifact(outcome.report_key)
        assert artifact is not None
        assert artifact.size_bytes > 10_000

        session = service.load_session(outcome.session_id, outcome.dashboard_url.split("token=")[1])
        assert session is not None
        assert session.briefing is not None

    def test_a_session_requires_the_right_token(self, service, spec, data_dir: Path) -> None:
        outcome = service.generate_report(spec, base_path=data_dir)
        assert service.load_session(outcome.session_id, None) is None
        assert service.load_session(outcome.session_id, "wrong") is None

    def test_purge_reports_what_it_removed(self, service) -> None:
        removed = service.purge()
        assert set(removed) >= {"sessions", "artifacts"}


class TestStructuredLogging:
    """Structured fields must never collide with a LogRecord slot.

    A field named `name`, `module`, `filename` or `args` used to raise
    `KeyError: "Attempt to overwrite 'name' in LogRecord"` from inside the
    logging call, which surfaced as a 500 from whatever was being logged. The
    logger class renames colliding keys instead.
    """

    @pytest.mark.parametrize(
        "field", ["name", "module", "filename", "args", "message", "levelname", "lineno"]
    )
    def test_reserved_field_names_do_not_raise(self, field: str, caplog) -> None:
        from insight_engine.core.logging import get_logger

        logger = get_logger("tests.reserved")
        with caplog.at_level("INFO"):
            logger.info("event", extra={field: "value"})

        record = caplog.records[-1]
        assert getattr(record, f"field_{field}") == "value"

    def test_ordinary_fields_are_untouched(self, caplog) -> None:
        from insight_engine.core.logging import get_logger

        logger = get_logger("tests.ordinary")
        with caplog.at_level("INFO"):
            logger.info("event", extra={"upload_id": "abc", "rows": 12})

        record = caplog.records[-1]
        assert record.upload_id == "abc"
        assert record.rows == 12

    def test_credentials_are_redacted_from_structured_fields(self) -> None:
        from insight_engine.core.logging import redact

        cleaned = redact({"connection_string": "postgres://u:p@h/db", "rows": 3})
        assert cleaned["connection_string"] == "***"
        assert cleaned["rows"] == 3
