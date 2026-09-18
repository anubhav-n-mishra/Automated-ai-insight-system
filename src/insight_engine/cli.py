"""Command line interface.

The CLI runs the same :class:`~insight_engine.service.ReportService` as the HTTP
API, so a report produced in CI is byte-for-byte the pipeline a user sees in the
browser. It also makes the tool usable from a scheduler without standing up a
web server at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from insight_engine import __version__
from insight_engine.core.config import Settings, get_settings, reset_settings_cache
from insight_engine.core.errors import InsightEngineError
from insight_engine.core.logging import configure_logging
from insight_engine.domain.spec import AnalysisSpec, analysis_spec_json_schema, spec_from_mapping
from insight_engine.engine.formatting import describe_total, format_delta_pct, format_value
from insight_engine.llm.registry import build_provider
from insight_engine.service import ReportService
from insight_engine.sessions import FileSessionStore
from insight_engine.storage import LocalArtifactStore

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAILURE = 1


def load_spec_file(path: Path) -> AnalysisSpec:
    """Read a YAML or JSON specification."""
    if not path.is_file():
        raise InsightEngineError(f"Specification not found: {path}")
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise InsightEngineError(f"{path} does not contain a specification document")
    return spec_from_mapping(raw)


def build_service(settings: Settings) -> ReportService:
    settings.ensure_directories()
    return ReportService(
        settings=settings,
        artifacts=LocalArtifactStore(
            settings.reports_dir, retention_seconds=settings.artifact_ttl_hours * 3600
        ),
        sessions=FileSessionStore(settings.sessions_dir),
        provider=build_provider(settings),
    )


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_validate(args: argparse.Namespace) -> int:
    """Check a specification without touching the data."""
    spec = load_spec_file(Path(args.spec))
    print(f"Specification is valid: {spec.report.title}")
    print(f"  Sources         : {', '.join(spec.dataset.sources)}")
    print(f"  Metrics         : {', '.join(spec.metric_names)}")
    print(f"  Dimensions      : {', '.join(spec.report.dimensions) or '(none)'}")
    print(f"  Comparison      : {spec.report.comparison.describe()}")
    for warning in spec.warnings():
        print(f"  Warning         : {warning}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """Run an analysis and print the result."""
    settings = get_settings()
    spec = load_spec_file(Path(args.spec))
    service = build_service(settings)
    base_path = (
        Path(args.base_path).resolve() if args.base_path else Path(args.spec).resolve().parent
    )

    def progress(stage: str, fraction: float) -> None:
        if not args.quiet:
            print(f"  [{fraction * 100:5.1f}%] {stage}", file=sys.stderr)

    if args.no_report:
        result = service.analyse(
            spec,
            base_path=base_path,
            on_progress=progress,
            narrate=not args.no_narrative,
            confine_to_base=False,
        )
        if args.json:
            print(json.dumps(result.to_public_dict(), indent=2, default=str))
        else:
            _print_result(result)
        return EXIT_OK

    outcome = service.generate_report(
        spec,
        base_path=base_path,
        on_progress=progress,
        include_audio=args.audio,
        # The operator supplied this spec from their own shell; paths such as
        # ../data/events.csv are ordinary, not an attack.
        confine_to_base=False,
    )
    if args.json:
        print(json.dumps(outcome.to_public_dict(), indent=2, default=str))
        return EXIT_OK

    _print_result(outcome.result)
    artifact = service.fetch_artifact(outcome.report_key)
    print()
    print(f"Deck      : {artifact.local_path if artifact else outcome.report_filename}")
    print(f"Dashboard : {outcome.dashboard_url}")
    print("The dashboard link carries an access token and expires with the session.")
    return EXIT_OK


def _print_result(result: Any) -> None:
    narrative = result.narrative
    print()
    print("=" * 78)
    print(narrative.title if narrative else result.spec_title)
    print(result.period.describe())
    print("=" * 78)

    if narrative:
        print()
        print(narrative.headline)
        for bullet in narrative.bullets:
            print(f"  - {bullet}")
        if narrative.recommendation:
            print()
            print(f"Recommended: {narrative.recommendation}")

    print()
    print("TOTALS")
    for total in result.totals:
        print(f"  {describe_total(total)}")

    if result.insights:
        print()
        print("TOP MOVEMENTS")
        width = max((len(i.segment_label) for i in result.insights[:10]), default=10)
        for insight in result.insights[:10]:
            previous = format_value(
                insight.previous_value, insight.unit, precision=insight.precision
            )
            current = format_value(insight.current_value, insight.unit, precision=insight.precision)
            print(
                f"  {insight.segment_label:<{width}}  {insight.label:<14}"
                f"{previous:>12} -> {current:>12}"
                f"  {format_delta_pct(insight.delta_pct):>9}"
            )

    if result.drivers:
        print()
        print("DRIVERS")
        for attribution in result.drivers[:3]:
            note = "  (gains and losses partly cancel)" if attribution.offsetting else ""
            print(
                f"  {attribution.label}: net {attribution.total_delta:,.2f} across "
                f"{attribution.segment_count} segments; the top {len(attribution.drivers)} "
                f"account for {attribution.explained_pct:.0f}% of all movement{note}"
            )

    if result.warnings:
        print()
        print("WARNINGS")
        for warning in result.warnings:
            print(f"  ! {warning}")

    print()
    print(
        f"{result.row_count:,} rows, {result.segment_count:,} segments, "
        f"{result.duration_ms / 1000:.2f}s"
    )


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the HTTP server."""
    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn is not installed. Install it with: pip install 'insight-engine'",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    settings = get_settings()
    uvicorn.run(
        "insight_engine.api.app:create_app",
        factory=True,
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
        log_config=None,
        access_log=False,
    )
    return EXIT_OK


def cmd_schema(args: argparse.Namespace) -> int:
    """Emit the specification JSON Schema."""
    schema = analysis_spec_json_schema()
    text = json.dumps(schema, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Schema written to {args.output}")
    else:
        print(text)
    return EXIT_OK


def cmd_profile(args: argparse.Namespace) -> int:
    """Profile a delimited file and print the inferred roles."""
    import polars as pl

    from insight_engine.connectors.csv import sniff_delimiter
    from insight_engine.engine.profiling import profile_dataframe

    path = Path(args.file)
    if not path.is_file():
        print(f"No such file: {path}", file=sys.stderr)
        return EXIT_USAGE

    delimiter = args.delimiter or sniff_delimiter(path)
    frame = pl.read_csv(path, separator=delimiter, try_parse_dates=True, infer_schema_length=10_000)
    frame = frame.rename({column: column.lstrip("﻿").strip() for column in frame.columns})
    profile = profile_dataframe(frame)

    if args.json:
        print(json.dumps(profile.as_dict(), indent=2, default=str))
        return EXIT_OK

    print(
        f"{path.name}: {profile.row_count:,} rows, {len(profile.columns)} columns "
        f"(delimiter {delimiter!r})"
    )
    print()
    print(f"{'COLUMN':<28}{'ROLE':<12}{'TYPE':<12}{'DISTINCT':>10}  REASON")
    for column in profile.columns:
        print(
            f"{column.name[:27]:<28}{column.role:<12}{column.dtype[:11]:<12}"
            f"{column.distinct_count:>10}  {column.reason}"
        )
    if profile.suggested_comparison:
        print()
        print(f"Suggested comparison: {profile.suggested_comparison.describe()}")
    for note in profile.notes:
        print(f"Note: {note}")
    return EXIT_OK


def cmd_purge(args: argparse.Namespace) -> int:
    """Delete expired sessions and artifacts."""
    settings = get_settings()
    service = build_service(settings)
    removed = service.purge()
    print(f"Removed {removed['sessions']} session(s) and {removed['artifacts']} artifact(s).")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report the effective configuration and what is available."""
    settings = get_settings()
    provider = build_provider(settings)

    print(f"insight-engine {__version__}")
    print(f"  Python            : {sys.version.split()[0]}")
    print(f"  Environment       : {settings.environment}")
    print(f"  Data directory    : {settings.data_dir}")
    print(
        f"  Public base URL   : {settings.public_base_url or '(unset — links point at localhost)'}"
    )
    print(f"  Authentication    : {'enabled' if settings.api_keys else 'DISABLED'}")
    rate_limit = f"{settings.rate_limit_requests}/{settings.rate_limit_window_seconds}s"
    print(f"  Rate limit        : {rate_limit}")
    print(f"  Worker threads    : {settings.worker_threads}")
    print(f"  Narrative provider: {getattr(provider, 'name', 'template (deterministic)')}")
    print(f"  Remote SQL        : {'allowed' if settings.allow_remote_sql else 'blocked'}")
    print(f"  Voice synthesis   : {'configured' if settings.murf_api_key else 'browser fallback'}")

    print()
    print("Optional dependencies:")
    for module, purpose in [
        ("sqlalchemy", "SQL and database sources"),
        ("connectorx", "faster SQL reads"),
        ("opentelemetry", "distributed tracing"),
    ]:
        try:
            __import__(module)
        except ImportError:
            print(f"  [ ] {module:<16} {purpose}")
        else:
            print(f"  [x] {module:<16} {purpose}")

    if settings.is_production and not settings.api_keys:
        print()
        print("WARNING: production without API keys is an open compute proxy.", file=sys.stderr)
        return EXIT_FAILURE
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insight-engine",
        description="Turn period-over-period metric movements into ranked, explainable insights.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  insight-engine run examples/configs/marketing-csv.yaml\n"
            "  insight-engine profile data/events.csv\n"
            "  insight-engine validate my-report.yaml\n"
            "  insight-engine serve --port 8000\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"insight-engine {__version__}")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    parser.add_argument(
        "--log-format", default="text", choices=["text", "json"], help="Log output format"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run an analysis and generate a report")
    run.add_argument("spec", help="Path to the specification (YAML or JSON)")
    run.add_argument("--base-path", help="Directory relative data paths resolve against")
    run.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    run.add_argument("--no-report", action="store_true", help="Analyse only; do not render a deck")
    run.add_argument("--no-narrative", action="store_true", help="Skip narrative generation")
    run.add_argument("--audio", action="store_true", help="Render an audio briefing if configured")
    run.add_argument("-q", "--quiet", action="store_true", help="Suppress progress output")
    run.set_defaults(func=cmd_run)

    validate = sub.add_parser("validate", help="Validate a specification")
    validate.add_argument("spec")
    validate.set_defaults(func=cmd_validate)

    profile = sub.add_parser("profile", help="Infer column roles for a delimited file")
    profile.add_argument("file")
    profile.add_argument("--delimiter", help="Override delimiter detection")
    profile.add_argument("--json", action="store_true")
    profile.set_defaults(func=cmd_profile)

    serve = sub.add_parser("serve", help="Run the HTTP API and UI")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument(
        "--reload", action="store_true", help="Reload on source changes (development)"
    )
    serve.set_defaults(func=cmd_serve)

    schema = sub.add_parser("schema", help="Emit the specification JSON Schema")
    schema.add_argument("-o", "--output", help="Write to this file instead of stdout")
    schema.set_defaults(func=cmd_schema)

    sub.add_parser("purge", help="Delete expired sessions and artifacts").set_defaults(
        func=cmd_purge
    )
    sub.add_parser("doctor", help="Report the effective configuration").set_defaults(
        func=cmd_doctor
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    reset_settings_cache()
    settings = get_settings()
    configure_logging(args.log_level or settings.log_level, args.log_format)

    try:
        result: int = args.func(args)
        return result
    except InsightEngineError as error:
        print(f"error: {error.message}", file=sys.stderr)
        if error.context:
            print(f"       {json.dumps(error.context, default=str)}", file=sys.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
