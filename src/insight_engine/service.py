"""Application service: one analysis request, end to end.

This is the only place that knows the full choreography — analyse, narrate,
render, publish, voice — and it is transport-agnostic, so the CLI and the HTTP
API execute exactly the same path. The previous design inlined all of it into a
single 200-line request handler, which is why the CLI and the API drifted apart
and why nothing could be tested without a running server.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from insight_engine.connectors import SourcePolicy
from insight_engine.core.config import Settings
from insight_engine.core.logging import get_logger
from insight_engine.domain.results import AnalysisResult
from insight_engine.domain.spec import AnalysisSpec
from insight_engine.engine.narrative import generate_narrative
from insight_engine.engine.pipeline import ProgressCallback, run_analysis
from insight_engine.engine.qrcode_gen import build_qr_png
from insight_engine.engine.report import DEFAULT_THEME, Theme, build_deck
from insight_engine.engine.voice import Briefing, build_script, render_murf_audio
from insight_engine.llm.base import NarrativeProvider
from insight_engine.sessions import DashboardSession, SessionSecret, SessionStore
from insight_engine.storage.base import ArtifactStore, StoredArtifact

logger = get_logger("service")

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@dataclass(frozen=True)
class ReportOutcome:
    """Everything one run produced, ready to serialise."""

    result: AnalysisResult
    report_key: str
    report_filename: str
    session_id: str
    dashboard_url: str
    audio_key: str | None = None
    audio_url: str | None = None
    briefing: dict[str, Any] | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "dashboard_url": self.dashboard_url,
            "report": {
                "download_url": f"/api/v1/artifacts/{self.report_key}",
                "filename": self.report_filename,
                "media_type": PPTX_MEDIA_TYPE,
            },
            "audio": (
                {"url": self.audio_url, "kind": "murf"}
                if self.audio_url
                else {"url": None, "kind": "browser"}
            ),
            "analysis": self.result.to_public_dict(),
            "briefing": self.briefing,
        }


class ReportService:
    """Runs analyses and publishes their artifacts."""

    def __init__(
        self,
        *,
        settings: Settings,
        artifacts: ArtifactStore,
        sessions: SessionStore,
        provider: NarrativeProvider | None = None,
        theme: Theme = DEFAULT_THEME,
    ) -> None:
        self._settings = settings
        self._artifacts = artifacts
        self._sessions = sessions
        self._provider = provider
        self._theme = theme

    # ------------------------------------------------------------------ policy
    def source_policy(
        self, *, base_path: Path | None = None, confine_to_base: bool = True
    ) -> SourcePolicy:
        """Translate deployment settings into connector permissions."""
        settings = self._settings
        return SourcePolicy(
            allow_remote_sql=settings.allow_remote_sql,
            allowed_drivers=frozenset(settings.allowed_sql_drivers),
            allowed_hosts=frozenset(settings.allowed_sql_hosts),
            max_rows=settings.max_rows,
            statement_timeout_seconds=settings.sql_statement_timeout_seconds,
            base_path=base_path,
            confine_to_base=confine_to_base,
        )

    # --------------------------------------------------------------- analysis
    def analyse(
        self,
        spec: AnalysisSpec,
        *,
        base_path: Path | None = None,
        on_progress: ProgressCallback | None = None,
        narrate: bool = True,
        confine_to_base: bool = True,
    ) -> AnalysisResult:
        """Run the pipeline and attach a narrative."""
        provider = self._provider if narrate else None
        return run_analysis(
            spec,
            policy=self.source_policy(base_path=base_path, confine_to_base=confine_to_base),
            narrator=(lambda result: generate_narrative(result, provider)),
            on_progress=on_progress,
        )

    # ----------------------------------------------------------------- publish
    def generate_report(
        self,
        spec: AnalysisSpec,
        *,
        base_path: Path | None = None,
        on_progress: ProgressCallback | None = None,
        include_audio: bool = True,
        confine_to_base: bool = True,
    ) -> ReportOutcome:
        """Analyse, publish a session, render the deck and (optionally) audio."""
        result = self.analyse(
            spec, base_path=base_path, on_progress=on_progress, confine_to_base=confine_to_base
        )

        if on_progress:
            on_progress("publishing", 0.92)

        # The session is created before the deck so the deck can carry a working
        # QR code for it. The token exists only here and in the share link.
        secret = SessionSecret.mint()
        dashboard_url = self.dashboard_url(secret)

        briefing = Briefing(segments=build_script(result))
        audio_key: str | None = None
        audio_url: str | None = None

        if include_audio and self._settings.murf_api_key:
            audio = render_murf_audio(
                briefing.full_text,
                api_key=self._settings.murf_api_key.get_secret_value(),
                voice_id=self._settings.murf_voice_id,
                timeout_seconds=self._settings.voice_timeout_seconds,
            )
            if audio:
                audio_key = f"briefing-{secret.session_id}.mp3"
                self._artifacts.put(
                    audio,
                    key=audio_key,
                    media_type="audio/mpeg",
                    download_name=audio_key,
                )
                audio_url = f"/api/v1/artifacts/{audio_key}"
                briefing.audio_kind = "murf"
                briefing.audio_url = audio_url
                briefing.voice_id = self._settings.murf_voice_id

        if on_progress:
            on_progress("rendering", 0.96)

        qr_png = build_qr_png(dashboard_url)
        deck_path = self._render_deck(result, dashboard_url=dashboard_url, qr_png=qr_png)
        report_key = f"{uuid.uuid4().hex}.pptx"
        stored = self._artifacts.put(
            deck_path.read_bytes(),
            key=report_key,
            media_type=PPTX_MEDIA_TYPE,
            download_name=deck_path.name,
        )
        deck_path.unlink(missing_ok=True)

        session = DashboardSession.create(
            secret,
            title=(result.narrative.title if result.narrative else result.spec_title),
            payload=result.to_public_dict(),
            ttl_hours=self._settings.session_ttl_hours,
            briefing=briefing.as_dict(),
            report_key=report_key,
            audio_key=audio_key,
        )
        self._sessions.save(session)

        logger.info(
            "report published",
            extra={
                "session_id": session.session_id,
                "report_key": report_key,
                "insights": len(result.insights),
                "has_audio": bool(audio_key),
            },
        )

        if on_progress:
            on_progress("complete", 1.0)

        return ReportOutcome(
            result=result,
            report_key=report_key,
            report_filename=stored.download_name,
            session_id=session.session_id,
            dashboard_url=dashboard_url,
            audio_key=audio_key,
            audio_url=audio_url,
            briefing=briefing.as_dict(),
        )

    def _render_deck(self, result: AnalysisResult, *, dashboard_url: str, qr_png: bytes) -> Path:
        staging = self._settings.data_dir / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        return build_deck(
            result,
            staging,
            theme=self._theme,
            dashboard_url=dashboard_url,
            qr_png=qr_png,
        )

    def dashboard_url(self, secret: SessionSecret) -> str:
        """Shareable dashboard link.

        Built from ``public_base_url`` when set. Without it the link points at
        the server's own host, which is why production refuses to start until an
        operator configures it — a QR code that resolves to ``localhost`` is
        useless the moment it leaves the machine that made it.
        """
        base = self._settings.base_url()
        return f"{base}/d/{secret.session_id}?token={secret.token}"

    # --------------------------------------------------------------- artifacts
    def fetch_artifact(self, key: str) -> StoredArtifact | None:
        """Look up a stored report or briefing by key."""
        return self._artifacts.get(key)

    # ---------------------------------------------------------------- sessions
    def load_session(self, session_id: str, token: str | None) -> DashboardSession | None:
        """Fetch a session, enforcing the token.

        A token is always required. The previous implementation treated a
        missing token as "skip the check", so omitting the query parameter
        entirely granted access to any session id.
        """
        session = self._sessions.load(session_id)
        if session is None:
            return None
        if not session.verify(token):
            logger.warning("session token rejected", extra={"session_id": session_id})
            return None
        session.view_count += 1
        self._sessions.save(session)
        return session

    def purge(self) -> dict[str, int]:
        """Drop expired sessions and artifacts. Run on a timer."""
        return {
            "sessions": self._sessions.purge_expired(),
            "artifacts": self._artifacts.purge_expired(),
            "checked_at": int(datetime.now(timezone.utc).timestamp()),
        }
