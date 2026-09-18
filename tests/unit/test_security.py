"""Authentication, token handling and rate limiting."""

from __future__ import annotations

import time

import pytest
from pydantic import SecretStr

from insight_engine.api.security import ANONYMOUS, SlidingWindowRateLimiter, authenticate
from insight_engine.api.uploads import UploadStore, sanitize_filename
from insight_engine.core.config import Settings
from insight_engine.core.errors import (
    AuthError,
    InvalidUploadError,
    RateLimitedError,
    StorageError,
)
from insight_engine.sessions.models import DashboardSession, SessionSecret


class TestAuthentication:
    def test_open_deployment_yields_anonymous(self) -> None:
        assert authenticate(Settings(api_keys=[]), None) is ANONYMOUS

    def test_missing_header_is_rejected_when_keys_are_configured(self) -> None:
        with pytest.raises(AuthError, match="Missing"):
            authenticate(Settings(api_keys=[SecretStr("secret")]), None)

    def test_wrong_key_is_rejected(self) -> None:
        with pytest.raises(AuthError, match="Invalid"):
            authenticate(Settings(api_keys=[SecretStr("secret")]), "nope")

    def test_correct_key_is_accepted_and_fingerprinted(self) -> None:
        principal = authenticate(Settings(api_keys=[SecretStr("secret")]), "secret")
        assert principal.authenticated
        # The raw key must never become an identifier that gets logged.
        assert principal.key_id != "secret"
        assert len(principal.key_id) == 12

    def test_any_configured_key_works(self) -> None:
        settings = Settings(api_keys=[SecretStr("a"), SecretStr("b")])
        assert authenticate(settings, "b").authenticated

    def test_keys_parse_from_a_comma_separated_string(self) -> None:
        settings = Settings(api_keys="one, two ,three")
        assert [key.get_secret_value() for key in settings.api_keys] == ["one", "two", "three"]


class TestSessionTokens:
    def test_plaintext_token_is_never_stored(self) -> None:
        secret = SessionSecret.mint()
        session = DashboardSession.create(secret, title="t", payload={})
        assert secret.token not in str(session.to_record())
        assert session.token_hash != secret.token

    def test_verification(self) -> None:
        secret = SessionSecret.mint()
        session = DashboardSession.create(secret, title="t", payload={})
        assert session.verify(secret.token)
        assert not session.verify("wrong")

    def test_missing_token_never_verifies(self) -> None:
        """The auth bypass: a missing token used to skip the check entirely."""
        secret = SessionSecret.mint()
        session = DashboardSession.create(secret, title="t", payload={})
        assert not session.verify(None)
        assert not session.verify("")

    def test_expiry(self) -> None:
        from datetime import datetime, timedelta, timezone

        secret = SessionSecret.mint()
        session = DashboardSession.create(secret, title="t", payload={}, ttl_hours=1)
        assert not session.is_expired()
        assert session.is_expired(now=datetime.now(timezone.utc) + timedelta(hours=2))


class TestRateLimiter:
    def test_allows_up_to_the_limit_then_refuses(self) -> None:
        limiter = SlidingWindowRateLimiter(limit=3, window_seconds=60)
        for _ in range(3):
            limiter.check("caller")
        with pytest.raises(RateLimitedError) as error:
            limiter.check("caller")
        assert error.value.retry_after > 0

    def test_buckets_are_independent(self) -> None:
        limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60)
        limiter.check("a")
        limiter.check("b")

    def test_window_slides(self) -> None:
        limiter = SlidingWindowRateLimiter(limit=1, window_seconds=1)
        limiter.check("caller")
        time.sleep(1.05)
        limiter.check("caller")

    def test_zero_limit_disables(self) -> None:
        limiter = SlidingWindowRateLimiter(limit=0, window_seconds=60)
        assert not limiter.enabled
        for _ in range(50):
            limiter.check("caller")


class TestUploadSafety:
    @pytest.mark.parametrize(
        ("raw", "expected_missing"),
        [("../../etc/passwd", ".."), ("/etc/shadow", "/"), ("a\\b\\c.csv", "\\")],
    )
    def test_filenames_are_reduced_to_labels(self, raw: str, expected_missing: str) -> None:
        assert expected_missing not in sanitize_filename(raw)

    def test_uploads_are_stored_under_generated_ids(self, tmp_path) -> None:
        """The path-traversal fix.

        The previous handler wrote to ``tmp_dir / upload_file.filename``, so a
        request could name its upload ``../../app/main.py``.
        """
        store = UploadStore(tmp_path, max_bytes=1024)
        staged = store.save(b"a,b\n1,2\n", original_name="../../../evil.csv")
        assert staged.path.parent.parent == tmp_path
        assert staged.path.name == "data.csv"
        assert staged.path.is_relative_to(tmp_path)

    def test_rejects_oversized_uploads(self, tmp_path) -> None:
        from insight_engine.core.errors import PayloadTooLargeError

        store = UploadStore(tmp_path, max_bytes=8)
        with pytest.raises(PayloadTooLargeError):
            store.save(b"x" * 100, original_name="big.csv")

    def test_rejects_binary_masquerading_as_csv(self, tmp_path) -> None:
        store = UploadStore(tmp_path, max_bytes=1024)
        with pytest.raises(InvalidUploadError, match="not delimited text"):
            store.save(b"PK\x03\x04binary", original_name="book.csv")

    def test_rejects_traversal_in_an_upload_id(self, tmp_path) -> None:
        store = UploadStore(tmp_path, max_bytes=1024)
        with pytest.raises(StorageError, match="Invalid upload id"):
            store.directory_for("../../etc")


class TestSourcePolicy:
    def test_confined_paths_cannot_escape(self, tmp_path) -> None:
        from insight_engine.connectors.base import SourcePolicy
        from insight_engine.core.errors import SourceNotAllowedError

        policy = SourcePolicy(base_path=tmp_path)
        with pytest.raises(SourceNotAllowedError):
            policy.resolve_path("../../etc/passwd")

    def test_unconfined_policy_allows_relative_parents(self, tmp_path) -> None:
        from insight_engine.connectors.base import SourcePolicy

        policy = SourcePolicy(base_path=tmp_path / "nested", confine_to_base=False)
        assert policy.resolve_path("../outside.csv").name == "outside.csv"

    def test_remote_sql_is_blocked_by_default(self) -> None:
        from insight_engine.connectors.base import SourcePolicy
        from insight_engine.core.errors import SourceNotAllowedError

        with pytest.raises(SourceNotAllowedError, match="disabled"):
            SourcePolicy().check_host("db.internal")

    def test_host_allowlist_is_enforced(self) -> None:
        from insight_engine.connectors.base import SourcePolicy
        from insight_engine.core.errors import SourceNotAllowedError

        policy = SourcePolicy(allow_remote_sql=True, allowed_hosts=frozenset({"db.example.com"}))
        policy.check_host("db.example.com")
        with pytest.raises(SourceNotAllowedError, match="allowlist"):
            policy.check_host("169.254.169.254")

    def test_driver_allowlist_is_enforced(self) -> None:
        from insight_engine.connectors.base import SourcePolicy
        from insight_engine.core.errors import SourceNotAllowedError

        policy = SourcePolicy(allowed_drivers=frozenset({"postgresql"}))
        with pytest.raises(SourceNotAllowedError, match="not enabled"):
            policy.check_driver("mysql")


class TestProductionGuardrails:
    def test_production_requires_api_keys_and_a_public_url(self) -> None:
        from insight_engine.core.errors import ConfigurationError

        with pytest.raises(ConfigurationError) as error:
            Settings(environment="production")
        problems = " ".join(error.value.context["problems"])
        assert "API_KEYS" in problems
        assert "PUBLIC_BASE_URL" in problems

    def test_production_rejects_wildcard_cors(self) -> None:
        from insight_engine.core.errors import ConfigurationError

        with pytest.raises(ConfigurationError, match="cors"):
            Settings(
                environment="production",
                api_keys=[SecretStr("k")],
                public_base_url="https://x.test",
                cors_allow_origins=["*"],
            )

    def test_a_sound_production_config_boots(self) -> None:
        settings = Settings(
            environment="production",
            api_keys=[SecretStr("k")],
            public_base_url="https://reports.example.com",
        )
        assert settings.base_url() == "https://reports.example.com"
