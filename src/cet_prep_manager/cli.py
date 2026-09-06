"""Command-line interface for CET Prep Manager."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Annotated, Any

import typer

from cet_prep_manager import __version__
from cet_prep_manager.analytics import build_learner_state_snapshot, get_intervention_response
from cet_prep_manager.db import (
    DuplicateIdempotencyKeyError,
    RecordNotFoundError,
    ValidationError,
    create_error_event,
    create_exam_attempt,
    create_or_update_learner_profile,
    create_plan_version,
    create_subjective_assessment,
    create_training_session,
    get_active_learner_profile,
    get_active_plan,
    get_connection,
    get_default_db_path,
    get_exam_attempt,
    get_learner_profile,
    get_plan_adherence,
    get_plan_version,
    get_subjective_assessment,
    init_db,
    list_error_events,
    list_exam_attempts,
    list_plan_versions,
    list_subjective_assessments,
    list_training_sessions,
    update_error_event_state,
    update_plan_item_status,
)
from cet_prep_manager.exporter import generate_excel_report
from cet_prep_manager.models import (
    ConfidenceLevel,
    ErrorEventModel,
    ErrorResolvedState,
    PlanItemModel,
    PlanItemStatus,
    PlanVersionModel,
    SectionName,
    SubjectiveAssessmentModel,
    SubjectiveSectionName,
    TrainingSessionModel,
    get_band_info_for_score,
)
from cet_prep_manager.reporter import write_weekly_report

app = typer.Typer(
    name="cetpm",
    help="Local-first CET-4/CET-6 preparation manager.",
    no_args_is_help=True,
    add_completion=False,
)

profile_app = typer.Typer(
    name="profile",
    help="Manage learner profile.",
    no_args_is_help=True,
)
app.add_typer(profile_app, name="profile")

attempt_app = typer.Typer(
    name="attempt",
    help="Record and query exam attempts.",
    no_args_is_help=True,
)
app.add_typer(attempt_app, name="attempt")

assessment_app = typer.Typer(
    name="assessment",
    help="Record and view subjective assessments (writing/translation).",
    no_args_is_help=True,
)
app.add_typer(assessment_app, name="assessment")

errors_app = typer.Typer(
    name="errors",
    help="Record, query, and update learner error events.",
    no_args_is_help=True,
)
app.add_typer(errors_app, name="errors")
app.add_typer(errors_app, name="error")

plan_app = typer.Typer(
    name="plan",
    help="Manage dynamic preparation plans, versions, and items.",
    no_args_is_help=True,
)
app.add_typer(plan_app, name="plan")

training_app = typer.Typer(
    name="training",
    help="Record and query actual training sessions.",
    no_args_is_help=True,
)
app.add_typer(training_app, name="training")

analytics_app = typer.Typer(
    name="analytics",
    help="Inspect deterministic balance and intervention analytics.",
    no_args_is_help=True,
)
app.add_typer(analytics_app, name="analytics")

report_app = typer.Typer(
    name="report",
    help="Generate deterministic learner reports.",
    no_args_is_help=True,
)
app.add_typer(report_app, name="report")


def _get_conn(db_path: str | None) -> sqlite3.Connection:
    target = db_path if db_path else str(get_default_db_path())
    init_db(target)
    return get_connection(target)


def _parse_json_payload(raw: str) -> dict[str, Any]:
    trimmed = raw.strip()
    if trimmed.startswith("@"):
        path = Path(trimmed[1:]).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"JSON payload file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))
    path = Path(trimmed)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(trimmed)


@app.callback()
def root() -> None:
    """Local-first CET-4/CET-6 preparation manager."""


def _resolve_learner(conn: sqlite3.Connection, learner_id: str | None) -> str:
    if learner_id:
        if get_learner_profile(conn, learner_id) is None:
            raise RecordNotFoundError(f"Learner profile '{learner_id}' not found")
        return learner_id
    active = get_active_learner_profile(conn)
    if active is None:
        raise RecordNotFoundError(
            "No learner profile found. Create one first using 'cetpm profile update --exam CET6'."
        )
    return str(active["learner_id"])


@app.command()
def version(
    short: Annotated[
        bool,
        typer.Option("--short", help="Print only the version number."),
    ] = False,
) -> None:
    """Show the installed CET Prep Manager version."""
    if short:
        typer.echo(__version__)
    else:
        typer.echo(f"CET Prep Manager {__version__}")


@app.command()
def init(
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Initialize the CET Prep Manager database and apply migrations."""
    target = db_path if db_path else str(get_default_db_path())
    schema_ver = init_db(target)
    typer.echo(f"Initialized database at {target} (schema version {schema_ver}).")


@training_app.command("add")
def training_add(
    record_json: Annotated[
        str | None,
        typer.Option("--json-input", help="JSON string or @file containing a training session."),
    ] = None,
    module: Annotated[
        str | None,
        typer.Option("--module", "-m", help="Training module."),
    ] = None,
    activity_type: Annotated[
        str | None,
        typer.Option("--activity-type", help="Concrete training activity."),
    ] = None,
    minutes: Annotated[
        int | None,
        typer.Option("--minutes", min=0, help="Training duration in minutes."),
    ] = None,
    duration_seconds: Annotated[
        int | None,
        typer.Option("--duration-seconds", min=0, help="Training duration in seconds."),
    ] = None,
    started_at: Annotated[
        str | None,
        typer.Option("--started-at", help="ISO 8601 training start time."),
    ] = None,
    planned_item_id: Annotated[
        str | None,
        typer.Option("--planned-item-id", help="Optional associated plan item ID."),
    ] = None,
    completed: Annotated[
        bool,
        typer.Option("--completed/--not-completed", help="Whether the session was completed."),
    ] = True,
    notes: Annotated[
        str | None,
        typer.Option("--notes", help="Optional training notes."),
    ] = None,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output the stored session as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Record one actual training session."""
    conn = _get_conn(db_path)
    try:
        payload: dict[str, Any] = {}
        if record_json:
            try:
                payload = _parse_json_payload(record_json)
            except Exception as exc:
                typer.echo(
                    json.dumps(
                        {"error": f"Invalid JSON payload: {exc}"},
                        ensure_ascii=False,
                    )
                )
                raise typer.Exit(code=1) from exc

        try:
            target_learner = _resolve_learner(conn, learner_id or payload.get("learner_id"))
            raw_duration = (
                duration_seconds
                if duration_seconds is not None
                else payload.get("duration_seconds")
            )
            if minutes is not None:
                if raw_duration is not None:
                    raise ValidationError("Use either --minutes or --duration-seconds, not both")
                raw_duration = minutes * 60
            model = TrainingSessionModel(
                learner_id=target_learner,
                planned_item_id=planned_item_id or payload.get("planned_item_id"),
                started_at=(
                    started_at
                    or payload.get("started_at")
                    or datetime.now(timezone.utc).isoformat()
                ),
                duration_seconds=raw_duration,
                module=module or payload.get("module"),
                activity_type=activity_type or payload.get("activity_type"),
                completed=payload.get("completed", completed),
                notes=notes or payload.get("notes"),
            )
            created = create_training_session(
                conn,
                learner_id=target_learner,
                planned_item_id=model.planned_item_id,
                started_at=model.started_at,
                duration_seconds=model.duration_seconds,
                module=model.module.value if model.module else None,
                activity_type=model.activity_type,
                completed=model.completed,
                notes=model.notes,
            )
        except (ValidationError, RecordNotFoundError, ValueError) as exc:
            if as_json or record_json:
                typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False))
            else:
                typer.secho(f"Validation error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if as_json or record_json:
            typer.echo(json.dumps(created, indent=2, ensure_ascii=False))
        else:
            typer.secho("✓ Training Session Recorded", fg=typer.colors.GREEN, bold=True)
            typer.echo(f"ID:       {created['session_id']}")
            typer.echo(f"Module:   {created.get('module') or 'Not set'}")
            typer.echo(f"Duration: {created.get('duration_seconds') or 0} seconds")
    finally:
        conn.close()


@training_app.command("list")
def training_list(
    module: Annotated[
        str | None,
        typer.Option("--module", "-m", help="Filter by training module."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", min=1, help="Maximum sessions to return."),
    ] = 100,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output sessions as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """List recorded training sessions."""
    conn = _get_conn(db_path)
    try:
        try:
            target_learner = _resolve_learner(conn, learner_id)
            sessions = list_training_sessions(
                conn,
                target_learner,
                module=module,
                limit=limit,
            )
        except (ValidationError, RecordNotFoundError) as exc:
            if as_json:
                typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False))
            else:
                typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if as_json:
            typer.echo(json.dumps(sessions, indent=2, ensure_ascii=False))
        elif not sessions:
            typer.echo("No training sessions found.")
        else:
            for session in sessions:
                typer.echo(
                    f"{session['session_id']} | {session['started_at']} | "
                    f"{session.get('module') or '-'} | {session.get('activity_type') or '-'} | "
                    f"{session.get('duration_seconds') or 0}s | "
                    f"{'completed' if session.get('completed') else 'not completed'}"
                )
    finally:
        conn.close()


@analytics_app.command("balance")
def analytics_balance(
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output module balance as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Show diagnostic cross-module balance from latest comparable observations."""
    conn = _get_conn(db_path)
    try:
        try:
            target_learner = _resolve_learner(conn, learner_id)
            balance = build_learner_state_snapshot(conn, target_learner).module_balance
        except RecordNotFoundError as exc:
            if as_json:
                typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False))
            else:
                typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if as_json:
            typer.echo(balance.model_dump_json(indent=2))
        elif balance.gap is None:
            typer.echo("Insufficient comparable module observations.")
        else:
            typer.echo(
                f"Strongest: {balance.strongest_module}; weakest: {balance.weakest_module}; "
                f"normalized gap: {balance.gap:.4f} (diagnostic only)."
            )
    finally:
        conn.close()


@analytics_app.command("intervention")
def analytics_intervention(
    session_id: Annotated[
        str,
        typer.Argument(help="Training session ID used as intervention start."),
    ],
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output intervention response as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Compare two observations before and after a recorded training intervention."""
    conn = _get_conn(db_path)
    try:
        try:
            target_learner = _resolve_learner(conn, learner_id)
            response = get_intervention_response(conn, target_learner, session_id)
        except RecordNotFoundError as exc:
            if as_json:
                typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False))
            else:
                typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if as_json:
            typer.echo(json.dumps(response, indent=2, ensure_ascii=False))
        elif not response.get("supported"):
            typer.echo(response["reason"])
        elif not response["sufficient"]:
            typer.echo(
                f"Insufficient data: {response['pre_n']} before and {response['post_n']} after; "
                "2 each required."
            )
        else:
            typer.echo(
                f"Exploratory response: {response['direction']}; "
                f"delta {response['delta']:+.4f} {response['unit']}."
            )
    finally:
        conn.close()


@report_app.command("weekly")
def report_weekly(
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Markdown output path."),
    ] = None,
    days: Annotated[
        int,
        typer.Option("--days", min=1, help="Reporting window in days."),
    ] = 7,
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Optional ISO 8601 report end timestamp."),
    ] = None,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output report metadata and deterministic data as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Generate a deterministic Markdown weekly report."""
    conn = _get_conn(db_path)
    try:
        try:
            target_learner = _resolve_learner(conn, learner_id)
            report_end = (
                datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
            )
            target = (
                Path(output).expanduser().resolve()
                if output
                else Path("exports", f"weekly_{target_learner[:8]}.md").resolve()
            )
            saved_path, data = write_weekly_report(
                conn,
                target_learner,
                target,
                days=days,
                as_of=report_end,
            )
        except (RecordNotFoundError, ValueError) as exc:
            if as_json:
                typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False))
            else:
                typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if as_json:
            typer.echo(
                json.dumps(
                    {"status": "success", "output_file": str(saved_path), "data": data},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            typer.secho("✓ Weekly Markdown Report Generated", fg=typer.colors.GREEN, bold=True)
            typer.echo(f"File Path: {saved_path}")
    finally:
        conn.close()


@app.command("status")
def status(
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active profile)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output status snapshot as JSON compliant with learner_state.schema.json."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Show the overall preparation status and analytics snapshot."""
    conn = _get_conn(db_path)
    try:
        target_learner = learner_id
        if not target_learner:
            active = get_active_learner_profile(conn)
            if active:
                target_learner = active["learner_id"]
            else:
                msg = "No learner profile found. Create one first using 'cetpm profile update --exam CET6'."
                if as_json:
                    typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
                else:
                    typer.secho(msg, fg=typer.colors.YELLOW)
                raise typer.Exit(code=1)

        snapshot = build_learner_state_snapshot(conn, target_learner)

        if as_json:
            typer.echo(snapshot.model_dump_json(indent=2))
        else:
            profile = get_learner_profile(conn, target_learner)
            display_name = profile.get("display_name") if profile else None
            name_str = f" ({display_name})" if display_name else ""
            typer.secho("======================================================", fg=typer.colors.CYAN, bold=True)
            typer.secho("     CET PREPARATION STATUS & ANALYTICS SNAPSHOT      ", fg=typer.colors.CYAN, bold=True)
            typer.secho("======================================================", fg=typer.colors.CYAN, bold=True)
            target_exam_val = getattr(snapshot.target_exam, "value", snapshot.target_exam)
            typer.echo(f"Learner ID:       {snapshot.learner_id}{name_str}")
            typer.echo(f"Target Exam:      {target_exam_val}")
            typer.echo(f"Target Date:      {snapshot.target_exam_date or 'Not set'}")
            typer.echo(f"Target Score:     {snapshot.target_reported_score or 'Not set'}")
            typer.echo(f"Active Plan ID:   {snapshot.active_plan_id or 'None'}")
            typer.echo(f"Snapshot Time:    {snapshot.generated_at[:19].replace('T', ' ')} UTC")
            typer.echo("")
            typer.secho("--- Module Performance & Analytics ---", bold=True)
            header = f"{'MODULE':<13} | {'N':<3} | {'LATEST':<8} | {'MA3':<8} | {'MA5':<8} | {'SLOPE':<8} | {'VOLATILITY':<10} | {'SUFFICIENCY'}"
            typer.echo(header)
            typer.echo("-" * len(header))

            for mod_name in ["listening", "reading", "writing", "translation"]:
                st = snapshot.modules.get(mod_name)
                if not st:
                    continue
                is_subj = mod_name in ("writing", "translation")
                if st.latest is not None:
                    latest_str = f"{st.latest:.1f}/15" if is_subj else f"{st.latest*100:.1f}%"
                else:
                    latest_str = "N/A"

                if st.ma3 is not None:
                    ma3_str = f"{st.ma3:.1f}/15" if is_subj else f"{st.ma3*100:.1f}%"
                else:
                    ma3_str = "N/A"

                if st.ma5 is not None:
                    ma5_str = f"{st.ma5:.1f}/15" if is_subj else f"{st.ma5*100:.1f}%"
                else:
                    ma5_str = "N/A"

                slope_str = f"{st.trend_slope:+.4f}" if st.trend_slope is not None else "N/A"
                vol_str = f"{st.volatility:.4f}" if st.volatility is not None else "N/A"
                suff_str = st.sufficiency.value

                typer.echo(
                    f"{mod_name.capitalize():<13} | {st.n:<3} | {latest_str:<8} | {ma3_str:<8} | "
                    f"{ma5_str:<8} | {slope_str:<8} | {vol_str:<10} | {suff_str}"
                )

            typer.echo("")
            balance = snapshot.module_balance
            if balance.gap is not None:
                typer.echo(
                    "Module balance (diagnostic only): "
                    f"strongest={balance.strongest_module}, weakest={balance.weakest_module}, "
                    f"normalized gap={balance.gap:.4f}"
                )
                typer.echo("")
            if snapshot.top_error_codes:
                typer.secho("--- Top Persistent Error Taxonomy Codes ---", bold=True)
                for idx, code in enumerate(snapshot.top_error_codes, start=1):
                    typer.echo(f"  {idx}. {code}")
            else:
                typer.echo("No error codes recorded yet.")
    finally:
        conn.close()


@app.command("export")
def export(
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Path to output Excel file (.xlsx)."),
    ] = None,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active profile)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output export result as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Export comprehensive Excel progress workbook with embedded longitudinal charts."""
    conn = _get_conn(db_path)
    try:
        target_learner = learner_id
        if not target_learner:
            active = get_active_learner_profile(conn)
            if active:
                target_learner = active["learner_id"]
            else:
                msg = "No learner profile found. Create one first using 'cetpm profile update --exam CET6'."
                if as_json:
                    typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
                else:
                    typer.secho(msg, fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)

        if output:
            out_file = Path(output).expanduser().resolve()
        else:
            default_dir = Path("exports").resolve()
            default_dir.mkdir(parents=True, exist_ok=True)
            out_file = default_dir / f"progress_{target_learner[:8]}.xlsx"

        saved_path = generate_excel_report(conn, target_learner, out_file)

        if as_json:
            result_data = {
                "status": "success",
                "learner_id": target_learner,
                "exported_file": str(saved_path),
                "sheets": [
                    "Overview",
                    "Mock History",
                    "Listening",
                    "Reading",
                    "Writing",
                    "Translation",
                    "Errors",
                    "Plan History",
                    "Weekly Summary",
                ],
                "charts_embedded": [
                    "Overview (Comprehensive Multi-Panel Progress)",
                    "Listening (Longitudinal Accuracy & Moving Averages)",
                    "Reading (Longitudinal Accuracy & Moving Averages)",
                    "Writing (Official-Aligned Bands & Uncertainty Range)",
                    "Translation (Official-Aligned Bands & Uncertainty Range)",
                    "Errors (Top Categories by Resolution State)",
                ],
            }
            typer.echo(json.dumps(result_data, indent=2, ensure_ascii=False))
        else:
            typer.secho("✓ Excel Progress Workbook Successfully Generated", fg=typer.colors.GREEN, bold=True)
            typer.echo(f"File Path:       {saved_path}")
            typer.echo(f"Learner ID:      {target_learner}")
            typer.echo("Sheets:          9 sheets (Overview, Mock History, Listening, Reading, Writing, Translation, Errors, Plan History, Weekly Summary)")
            typer.echo("Embedded Charts: 5 embedded longitudinal trend and distribution charts")
    finally:
        conn.close()


# =====================================================================
# Profile Commands
# =====================================================================


@profile_app.command("show")
def profile_show(
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Specific learner ID to inspect."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output profile as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Display the learner profile."""
    conn = _get_conn(db_path)
    try:
        if learner_id:
            profile = get_learner_profile(conn, learner_id)
        else:
            profile = get_active_learner_profile(conn)

        if not profile:
            if as_json:
                typer.echo(json.dumps({"error": "No learner profile found"}, ensure_ascii=False))
            else:
                typer.secho("No learner profile found. Run 'cetpm profile update' to create one.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)

        if as_json:
            typer.echo(json.dumps(profile, indent=2, ensure_ascii=False))
        else:
            typer.echo("=== Learner Profile ===")
            typer.echo(f"Learner ID:            {profile['learner_id']}")
            typer.echo(f"Display Name:          {profile.get('display_name') or 'N/A'}")
            typer.echo(f"Target Exam:           {profile['target_exam']}")
            typer.echo(f"Target Exam Date:      {profile.get('target_exam_date') or 'N/A'}")
            typer.echo(f"Target Reported Score: {profile.get('target_reported_score') or 'N/A'}")
            typer.echo(f"Daily Minutes:         {profile.get('daily_minutes') or 'N/A'}")
            typer.echo(f"Updated At:            {profile['updated_at']}")
    finally:
        conn.close()


@profile_app.command("update")
def profile_update(
    name: Annotated[
        str | None,
        typer.Option("--name", help="Learner display name."),
    ] = None,
    exam: Annotated[
        str | None,
        typer.Option("--exam", help="Target exam level: CET4 or CET6."),
    ] = None,
    target_date: Annotated[
        str | None,
        typer.Option("--target-date", help="Target exam date (YYYY-MM-DD)."),
    ] = None,
    target_score: Annotated[
        int | None,
        typer.Option("--target-score", help="Target reported score (e.g. 550)."),
    ] = None,
    daily_minutes: Annotated[
        int | None,
        typer.Option("--daily-minutes", help="Daily study minutes."),
    ] = None,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID to update."),
    ] = None,
    json_input: Annotated[
        str | None,
        typer.Option("--json-input", help="JSON string or @file containing profile data."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output updated profile as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Create or update learner profile."""
    conn = _get_conn(db_path)
    try:
        payload: dict[str, Any] = {}
        if json_input:
            try:
                payload = _parse_json_payload(json_input)
            except Exception as exc:
                if as_json:
                    typer.echo(json.dumps({"error": f"Invalid JSON payload: {exc}"}, ensure_ascii=False))
                else:
                    typer.secho(f"Error parsing JSON payload: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1) from exc

        target_name = name or payload.get("display_name")
        target_exam = exam or payload.get("target_exam")
        t_date = target_date or payload.get("target_exam_date")
        t_score = target_score if target_score is not None else payload.get("target_reported_score")
        d_min = daily_minutes if daily_minutes is not None else payload.get("daily_minutes")
        t_id = learner_id or payload.get("learner_id")

        if not target_exam:
            active = get_active_learner_profile(conn)
            if active:
                target_exam = active["target_exam"]
            else:
                target_exam = "CET6"

        try:
            profile = create_or_update_learner_profile(
                conn,
                learner_id=t_id,
                display_name=target_name,
                target_exam=target_exam,
                target_exam_date=t_date,
                target_reported_score=t_score,
                daily_minutes=d_min,
            )
        except (ValidationError, Exception) as exc:
            if as_json:
                typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False))
            else:
                typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if as_json:
            typer.echo(json.dumps(profile, indent=2, ensure_ascii=False))
        else:
            typer.secho(f"Updated learner profile: {profile['learner_id']} ({profile['target_exam']})", fg=typer.colors.GREEN)
    finally:
        conn.close()


# =====================================================================
# Attempt Commands
# =====================================================================


@attempt_app.command("add")
def attempt_add(
    record_json: Annotated[
        str | None,
        typer.Option("--json", "--json-input", help="JSON string or @file containing attempt record."),
    ] = None,
    exam: Annotated[
        str | None,
        typer.Option("--exam", help="Exam level: CET4 or CET6."),
    ] = None,
    type_: Annotated[
        str | None,
        typer.Option("--type", help="Attempt type: full_mock, section_practice, official_exam, diagnostic."),
    ] = None,
    source_key: Annotated[
        str | None,
        typer.Option("--source-key", help="Source identifier, e.g. CET6-2023-12-Set1."),
    ] = None,
    source_kind: Annotated[
        str | None,
        typer.Option("--source-kind", help="Source kind: past_paper, commercial_mock, generated, official_result, other."),
    ] = None,
    official_score: Annotated[
        int | None,
        typer.Option(
            "--official-score",
            min=0,
            max=710,
            help="Actual 0-710 CET reported score; valid only with --type official_exam.",
        ),
    ] = None,
    duration: Annotated[
        int | None,
        typer.Option("--duration", help="Duration in seconds."),
    ] = None,
    notes: Annotated[
        str | None,
        typer.Option("--notes", help="Notes or comments."),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option("--idempotency-key", help="Unique idempotency key for deduplication."),
    ] = None,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID."),
    ] = None,
    output_json: Annotated[
        bool,
        typer.Option("--output-json", help="Force output format as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Record a new exam attempt or mock session."""
    conn = _get_conn(db_path)
    try:
        payload: dict[str, Any] = {}
        is_json_invoked = False
        if record_json:
            is_json_invoked = True
            try:
                payload = _parse_json_payload(record_json)
            except Exception as exc:
                if output_json or is_json_invoked:
                    typer.echo(json.dumps({"error": f"Invalid JSON payload: {exc}"}, ensure_ascii=False))
                else:
                    typer.secho(f"Error parsing JSON payload: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1) from exc

        # Resolve learner ID
        target_learner = learner_id or payload.get("learner_id")
        if not target_learner:
            active = get_active_learner_profile(conn)
            if active:
                target_learner = active["learner_id"]
            else:
                msg = "No learner profile found. Create one first using 'cetpm profile update --exam CET6'."
                if output_json or is_json_invoked:
                    typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
                else:
                    typer.secho(msg, fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)

        t_exam = exam or payload.get("exam_level") or "CET6"
        t_type = type_ or payload.get("attempt_type") or "full_mock"
        t_source = source_key or payload.get("source_key")
        t_kind = source_kind or payload.get("source_kind")
        t_official_score = (
            official_score
            if official_score is not None
            else payload.get("official_reported_score")
        )
        t_dur = duration if duration is not None else payload.get("duration_seconds")
        t_notes = notes or payload.get("notes")
        t_idemp = idempotency_key or payload.get("idempotency_key")
        t_date = payload.get("attempted_at")

        sections = payload.get("sections") or payload.get("section_results")
        assessments = payload.get("subjective_assessments")
        errors = payload.get("error_events")

        try:
            attempt = create_exam_attempt(
                conn,
                learner_id=target_learner,
                attempted_at=t_date,
                exam_level=t_exam,
                attempt_type=t_type,
                source_key=t_source,
                source_kind=t_kind,
                official_reported_score=t_official_score,
                duration_seconds=t_dur,
                notes=t_notes,
                idempotency_key=t_idemp,
                section_results=sections,
                subjective_assessments=assessments,
                error_events=errors,
            )
        except DuplicateIdempotencyKeyError as exc:
            if output_json or is_json_invoked:
                typer.echo(json.dumps({"error": str(exc), "error_type": "DuplicateIdempotencyKeyError"}, ensure_ascii=False))
            else:
                typer.secho(f"Duplicate attempt: {exc}", fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(code=1) from exc
        except (ValidationError, RecordNotFoundError, Exception) as exc:
            if output_json or is_json_invoked:
                typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False))
            else:
                typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if output_json or is_json_invoked:
            typer.echo(json.dumps(attempt, indent=2, ensure_ascii=False))
        else:
            typer.secho(
                f"Recorded exam attempt: {attempt['attempt_id']} ({attempt['exam_level']} {attempt['attempt_type']})",
                fg=typer.colors.GREEN,
            )
            if attempt.get("section_results"):
                typer.echo(f"  Sections recorded: {len(attempt['section_results'])}")
    finally:
        conn.close()


@attempt_app.command("list")
def attempt_list(
    exam: Annotated[
        str | None,
        typer.Option("--exam", help="Filter by exam level: CET4 or CET6."),
    ] = None,
    type_: Annotated[
        str | None,
        typer.Option("--type", help="Filter by attempt type."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum number of attempts to return."),
    ] = 50,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output attempts as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """List exam attempts."""
    conn = _get_conn(db_path)
    try:
        target_learner = learner_id
        if not target_learner:
            active = get_active_learner_profile(conn)
            if active:
                target_learner = active["learner_id"]
            else:
                if as_json:
                    typer.echo("[]")
                else:
                    typer.secho("No learner profile found.", fg=typer.colors.YELLOW)
                return

        attempts = list_exam_attempts(
            conn,
            learner_id=target_learner,
            exam_level=exam,
            attempt_type=type_,
            limit=limit,
        )

        if as_json:
            typer.echo(json.dumps(attempts, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Total Attempts: {len(attempts)}")
            for att in attempts:
                src = att.get("source_key") or "N/A"
                dur = f"{att['duration_seconds']}s" if att.get("duration_seconds") else "N/A"
                typer.echo(
                    f"- {att['attempt_id'][:8]}... | {att['attempted_at'][:10]} | "
                    f"{att['exam_level']} {att['attempt_type']} | Source: {src} | Duration: {dur}"
                )
    finally:
        conn.close()


@attempt_app.command("show")
def attempt_show(
    attempt_id: Annotated[
        str,
        typer.Argument(help="Exam attempt ID to display."),
    ],
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output attempt as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Show details of a specific exam attempt."""
    conn = _get_conn(db_path)
    try:
        attempt = get_exam_attempt(conn, attempt_id, include_details=True)
        if not attempt:
            msg = f"Exam attempt '{attempt_id}' not found."
            if as_json:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        if as_json:
            typer.echo(json.dumps(attempt, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"=== Exam Attempt: {attempt['attempt_id']} ===")
            typer.echo(f"Level:       {attempt['exam_level']}")
            typer.echo(f"Type:        {attempt['attempt_type']}")
            typer.echo(f"Date:        {attempt['attempted_at']}")
            typer.echo(f"Source:      {attempt.get('source_key') or 'N/A'}")
            typer.echo(f"Notes:       {attempt.get('notes') or 'N/A'}")

            sections = attempt.get("section_results", [])
            if sections:
                typer.echo("\n--- Section Results ---")
                for s in sections:
                    acc = f"{s['accuracy']*100:.1f}%" if s.get("accuracy") is not None else "N/A"
                    typer.echo(f"  [{s['section']}] correct: {s.get('correct_count')}/{s.get('total_count')} ({acc})")

            subs = attempt.get("subjective_assessments", [])
            if subs:
                typer.echo("\n--- Subjective Assessments ---")
                for sub in subs:
                    typer.echo(
                        f"  [{sub['section']}] Est: {sub['estimated_score']}/15 "
                        f"(Band {sub['band_low']}–{sub['band_high']}, conf: {sub['confidence']})"
                    )

            errs = attempt.get("error_events", [])
            if errs:
                typer.echo("\n--- Error Events ---")
                for err in errs:
                    typer.echo(f"  [{err['section']}] {err['taxonomy_code']} (sev: {err['severity']})")
    finally:
        conn.close()


# =====================================================================
# Subjective Assessment Commands
# =====================================================================


@assessment_app.command("add")
def assessment_add(
    record_json: Annotated[
        str | None,
        typer.Option("--json-input", help="JSON string or @file containing subjective assessment."),
    ] = None,
    section: Annotated[
        str | None,
        typer.Option("--section", "-s", help="Section: writing or translation."),
    ] = None,
    score: Annotated[
        int | None,
        typer.Option("--score", help="Estimated score (1-15).", min=1, max=15),
    ] = None,
    score_low: Annotated[
        int | None,
        typer.Option("--score-low", help="Lower score bound (1-15).", min=1, max=15),
    ] = None,
    score_high: Annotated[
        int | None,
        typer.Option("--score-high", help="Upper score bound (1-15).", min=1, max=15),
    ] = None,
    confidence: Annotated[
        str,
        typer.Option("--confidence", help="Confidence level: low, medium, or high."),
    ] = "medium",
    rubric_version: Annotated[
        str,
        typer.Option("--rubric-version", help="Rubric version."),
    ] = "v1.0",
    assessor_model: Annotated[
        str,
        typer.Option("--assessor-model", help="Assessor model identifier."),
    ] = "human-or-cli",
    assessor_provider: Annotated[
        str | None,
        typer.Option("--assessor-provider", help="Assessor provider, e.g. openai, anthropic, google, local."),
    ] = None,
    skill_version: Annotated[
        str,
        typer.Option("--skill-version", help="Skill version."),
    ] = "0.1.0",
    rationale: Annotated[
        str | None,
        typer.Option("--rationale", help="Overall rationale or critique in markdown."),
    ] = None,
    revision: Annotated[
        str | None,
        typer.Option("--revision", help="Sample revision or model essay in markdown."),
    ] = None,
    diagnostic_json: Annotated[
        str | None,
        typer.Option("--diagnostic-json", help="Diagnostic dimensions JSON string or @file."),
    ] = None,
    attempt_id: Annotated[
        str | None,
        typer.Option("--attempt-id", help="Associated exam attempt ID."),
    ] = None,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner profile ID (defaults to active)."),
    ] = None,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Output created assessment as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Record a subjective assessment (writing or translation evaluation)."""
    conn = _get_conn(db_path)
    try:
        payload: dict[str, Any] = {}
        is_json_invoked = False
        if record_json:
            is_json_invoked = True
            try:
                payload = _parse_json_payload(record_json)
            except Exception as exc:
                if output_json or is_json_invoked:
                    typer.echo(json.dumps({"error": f"Invalid JSON payload: {exc}"}, ensure_ascii=False))
                else:
                    typer.secho(f"Error parsing JSON payload: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1) from exc

        # Resolve learner ID
        target_learner = learner_id or payload.get("learner_id")
        if not target_learner:
            active = get_active_learner_profile(conn)
            if active:
                target_learner = active["learner_id"]
            else:
                msg = "No learner profile found. Create one first using 'cetpm profile update --exam CET6'."
                if output_json or is_json_invoked:
                    typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
                else:
                    typer.secho(msg, fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)

        # Merge flags and payload
        t_section = section or payload.get("section")
        if not t_section:
            msg = "--section (-s) is required (writing or translation)."
            if output_json or is_json_invoked:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        t_score = score if score is not None else payload.get("estimated_score")
        if t_score is None:
            msg = "--score is required (integer between 1 and 15)."
            if output_json or is_json_invoked:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        t_score_low = score_low if score_low is not None else payload.get("score_low", t_score)
        t_score_high = score_high if score_high is not None else payload.get("score_high", t_score)
        t_conf = payload.get("confidence", confidence)
        t_rubric = payload.get("rubric_version", rubric_version)
        t_assessor_model = payload.get("assessor_model", assessor_model)
        t_assessor_provider = payload.get("assessor_provider", assessor_provider)
        t_skill_version = payload.get("skill_version", skill_version)
        t_attempt_id = attempt_id or payload.get("attempt_id")

        # Rationale & revision
        t_rationale = rationale or payload.get("overall_rationale") or payload.get("rationale_md")
        t_revision = revision or payload.get("revision") or payload.get("revision_md")

        # Diagnostic
        t_diag: dict[str, float] = {}
        if diagnostic_json:
            try:
                parsed_diag = _parse_json_payload(diagnostic_json)
                if isinstance(parsed_diag, dict):
                    t_diag = parsed_diag
            except Exception as exc:
                msg = f"Invalid diagnostic JSON: {exc}"
                if output_json or is_json_invoked:
                    typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
                else:
                    typer.secho(msg, fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1) from exc
        elif "diagnostic" in payload:
            t_diag = payload["diagnostic"]
        elif "diagnostic_json" in payload:
            raw_diag = payload["diagnostic_json"]
            if isinstance(raw_diag, str):
                try:
                    t_diag = json.loads(raw_diag)
                except Exception:
                    t_diag = {}
            elif isinstance(raw_diag, dict):
                t_diag = raw_diag

        # Validate with SubjectiveAssessmentModel
        try:
            model = SubjectiveAssessmentModel(
                learner_id=target_learner,
                attempt_id=t_attempt_id,
                section=t_section,
                estimated_score=t_score,
                score_low=t_score_low,
                score_high=t_score_high,
                confidence=t_conf,
                rubric_version=t_rubric,
                assessor_model=t_assessor_model,
                assessor_provider=t_assessor_provider,
                skill_version=t_skill_version,
                diagnostic=t_diag,
                overall_rationale=t_rationale,
                revision=t_revision,
            )
            created = create_subjective_assessment(conn, **model.to_repo_dict())
        except (ValidationError, ValueError) as exc:
            if output_json or is_json_invoked:
                typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False))
            else:
                typer.secho(f"Validation error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if output_json:
            typer.echo(json.dumps(created, indent=2, ensure_ascii=False))
        else:
            typer.secho("✓ Subjective Assessment Recorded", fg=typer.colors.GREEN, bold=True)
            typer.echo(f"ID:          {created['assessment_id']}")
            typer.echo(f"Learner:     {created['learner_id']}")
            if created.get("attempt_id"):
                typer.echo(f"Attempt:     {created['attempt_id']}")
            typer.echo(f"Section:     {created['section']}")
            typer.echo(
                f"Score:       {created['estimated_score']}/15 "
                f"(Range: {created['score_low']}–{created['score_high']})"
            )
            typer.echo(
                f"Band:        Anchor {created['anchor_band']} "
                f"[{created['band_low']}–{created['band_high']}]"
            )
            typer.echo(f"Confidence:  {created['confidence']}")
            typer.echo(f"Assessor:    {created['assessor_model']}")
            if created.get("rationale_md"):
                typer.echo(f"Rationale:   {created['rationale_md']}")
    finally:
        conn.close()


@assessment_app.command("show")
def assessment_show(
    assessment_id: Annotated[
        str,
        typer.Argument(help="Subjective assessment ID to view."),
    ],
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output assessment as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Show details of a subjective assessment."""
    conn = _get_conn(db_path)
    try:
        res = get_subjective_assessment(conn, assessment_id)
        if not res:
            msg = f"Subjective assessment '{assessment_id}' not found."
            if as_json:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        if as_json:
            typer.echo(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"=== Subjective Assessment: {res['assessment_id']} ===")
            typer.echo(f"Section:     {res['section']}")
            typer.echo(f"Learner:     {res['learner_id']}")
            typer.echo(f"Attempt:     {res.get('attempt_id') or 'N/A'}")
            typer.echo(
                f"Score:       {res['estimated_score']}/15 "
                f"(Range: {res['score_low']}–{res['score_high']})"
            )
            typer.echo(
                f"Band:        Anchor {res['anchor_band']} "
                f"[{res['band_low']}–{res['band_high']}]"
            )
            typer.echo(f"Confidence:  {res['confidence']}")
            typer.echo(f"Rubric:      {res['rubric_version']}")
            typer.echo(f"Assessor:    {res['assessor_model']}")
            typer.echo(f"Created At:  {res['created_at']}")
            if res.get("diagnostic_json"):
                typer.echo(f"Diagnostic:  {res['diagnostic_json']}")
            if res.get("rationale_md"):
                typer.echo(f"\nRationale:\n{res['rationale_md']}")
            if res.get("revision_md"):
                typer.echo(f"\nRevision:\n{res['revision_md']}")
    finally:
        conn.close()


@assessment_app.command("list")
def assessment_list(
    section: Annotated[
        str | None,
        typer.Option("--section", "-s", help="Filter by section: writing or translation."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Max number of assessments to list."),
    ] = 50,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output assessments as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """List subjective assessments for a learner."""
    conn = _get_conn(db_path)
    try:
        target_learner = learner_id
        if not target_learner:
            active = get_active_learner_profile(conn)
            if active:
                target_learner = active["learner_id"]
            else:
                if as_json:
                    typer.echo("[]")
                else:
                    typer.secho("No learner profile found.", fg=typer.colors.YELLOW)
                return

        results = list_subjective_assessments(
            conn,
            target_learner,
            section=section,
            limit=limit,
        )

        if as_json:
            typer.echo(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Total Assessments: {len(results)}")
            for sub in results:
                typer.echo(
                    f"- {sub['assessment_id'][:8]}... | {sub['created_at'][:10]} | "
                    f"[{sub['section']}] {sub['estimated_score']}/15 "
                    f"(Band {sub['band_low']}–{sub['band_high']}, conf: {sub['confidence']})"
                )
    finally:
        conn.close()


# =====================================================================
# Error Event Commands
# =====================================================================


@errors_app.command("add")
def errors_add(
    record_json: Annotated[
        str | None,
        typer.Option("--json-input", help="JSON string or @file containing error event."),
    ] = None,
    section: Annotated[
        str | None,
        typer.Option("--section", "-s", help="Section: listening, reading, writing, translation."),
    ] = None,
    taxonomy_code: Annotated[
        str | None,
        typer.Option("--code", "-c", "--taxonomy-code", help="Taxonomy code, e.g. voc.collocation."),
    ] = None,
    subtype: Annotated[
        str | None,
        typer.Option("--subtype", help="Subtype, e.g. section_a, news_report."),
    ] = None,
    severity: Annotated[
        int,
        typer.Option("--severity", min=1, max=3, help="Severity (1=minor, 2=moderate, 3=severe)."),
    ] = 1,
    evidence_note: Annotated[
        str | None,
        typer.Option("--note", "--evidence-note", help="Evidence note or description of the mistake."),
    ] = None,
    resolved_state: Annotated[
        str,
        typer.Option("--state", help="Resolved state: new, recurrent, improving, resolved."),
    ] = "new",
    attempt_id: Annotated[
        str | None,
        typer.Option("--attempt-id", help="Associated exam attempt ID."),
    ] = None,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active)."),
    ] = None,
    output_json: Annotated[
        bool,
        typer.Option("--json", help="Output created error as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Record an error event."""
    conn = _get_conn(db_path)
    try:
        payload: dict[str, Any] = {}
        is_json_invoked = False
        if record_json:
            is_json_invoked = True
            try:
                payload = _parse_json_payload(record_json)
            except Exception as exc:
                if output_json or is_json_invoked:
                    typer.echo(json.dumps({"error": f"Invalid JSON payload: {exc}"}, ensure_ascii=False))
                else:
                    typer.secho(f"Error parsing JSON payload: {exc}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1) from exc

        # Resolve learner ID
        target_learner = learner_id or payload.get("learner_id")
        if not target_learner:
            active = get_active_learner_profile(conn)
            if active:
                target_learner = active["learner_id"]
            else:
                msg = "No learner profile found. Create one first using 'cetpm profile update --exam CET6'."
                if output_json or is_json_invoked:
                    typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
                else:
                    typer.secho(msg, fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)

        t_section = section or payload.get("section")
        if not t_section:
            msg = "--section (-s) is required (listening, reading, writing, translation)."
            if output_json or is_json_invoked:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        t_code = taxonomy_code or payload.get("taxonomy_code") or payload.get("code")
        if not t_code:
            msg = "--code (-c) is required (e.g. voc.collocation, grammar.tense)."
            if output_json or is_json_invoked:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        t_subtype = subtype or payload.get("subtype")
        t_sev = payload.get("severity", severity)
        t_note = evidence_note or payload.get("evidence_note") or payload.get("note")
        t_state = payload.get("resolved_state", payload.get("state", resolved_state))
        t_attempt_id = attempt_id or payload.get("attempt_id")

        try:
            model = ErrorEventModel(
                learner_id=target_learner,
                attempt_id=t_attempt_id,
                section=t_section,
                subtype=t_subtype,
                taxonomy_code=t_code,
                severity=t_sev,
                evidence_note=t_note,
                resolved_state=t_state,
            )
            created = create_error_event(
                conn,
                learner_id=model.learner_id,
                attempt_id=model.attempt_id,
                section=model.section.value,
                subtype=model.subtype,
                taxonomy_code=model.taxonomy_code,
                severity=model.severity,
                evidence_note=model.evidence_note,
                resolved_state=model.resolved_state.value,
            )
        except (ValidationError, ValueError) as exc:
            if output_json or is_json_invoked:
                typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False))
            else:
                typer.secho(f"Validation error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if output_json:
            typer.echo(json.dumps(created, indent=2, ensure_ascii=False))
        else:
            typer.secho("✓ Error Event Recorded", fg=typer.colors.GREEN, bold=True)
            typer.echo(f"ID:        {created['error_id']}")
            typer.echo(f"Learner:   {created['learner_id']}")
            sub_str = f" ({created['subtype']})" if created.get("subtype") else ""
            typer.echo(f"Section:   {created['section']}{sub_str}")
            typer.echo(f"Code:      {created['taxonomy_code']}")
            typer.echo(f"Severity:  {created['severity']}")
            typer.echo(f"State:     {created['resolved_state']}")
            if created.get("evidence_note"):
                typer.echo(f"Note:      {created['evidence_note']}")
    finally:
        conn.close()


@errors_app.command("list")
def errors_list(
    section: Annotated[
        str | None,
        typer.Option("--section", "-s", help="Filter by section."),
    ] = None,
    taxonomy_code: Annotated[
        str | None,
        typer.Option("--code", "-c", "--taxonomy-code", help="Filter by taxonomy code."),
    ] = None,
    resolved_state: Annotated[
        str | None,
        typer.Option("--state", help="Filter by state: new, recurrent, improving, resolved."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", help="Max number of errors to list."),
    ] = None,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output errors as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """List error events for a learner."""
    conn = _get_conn(db_path)
    try:
        target_learner = learner_id
        if not target_learner:
            active = get_active_learner_profile(conn)
            if active:
                target_learner = active["learner_id"]
            else:
                if as_json:
                    typer.echo("[]")
                else:
                    typer.secho("No learner profile found.", fg=typer.colors.YELLOW)
                return

        results = list_error_events(
            conn,
            target_learner,
            section=section,
            taxonomy_code=taxonomy_code,
            resolved_state=resolved_state,
        )

        if limit is not None:
            results = results[:limit]

        if as_json:
            typer.echo(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Total Error Events: {len(results)}")
            for err in results:
                sub_str = f".{err['subtype']}" if err.get("subtype") else ""
                note_str = f" | Note: {err['evidence_note']}" if err.get("evidence_note") else ""
                typer.echo(
                    f"- {err['error_id'][:8]}... | [{err['section']}{sub_str}] "
                    f"{err['taxonomy_code']} (sev: {err['severity']}, state: {err['resolved_state']}){note_str}"
                )
    finally:
        conn.close()


@errors_app.command("update")
def errors_update(
    error_id: Annotated[
        str,
        typer.Argument(help="Error event ID to update."),
    ],
    state: Annotated[
        str,
        typer.Option("--state", help="New resolved state: new, recurrent, improving, resolved."),
    ],
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output updated error as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Update an error event's resolved state."""
    conn = _get_conn(db_path)
    try:
        try:
            updated = update_error_event_state(conn, error_id, state)
        except RecordNotFoundError as exc:
            msg = str(exc)
            if as_json:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        except (ValidationError, ValueError) as exc:
            msg = str(exc)
            if as_json:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(f"Validation error: {msg}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if as_json:
            typer.echo(json.dumps(updated, indent=2, ensure_ascii=False))
        else:
            typer.secho(f"✓ Error {error_id[:8]}... updated to state '{state}'", fg=typer.colors.GREEN)
    finally:
        conn.close()


@errors_app.command("resolve")
def errors_resolve(
    error_id: Annotated[
        str,
        typer.Argument(help="Error event ID to resolve."),
    ],
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output updated error as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Mark an error event as resolved."""
    conn = _get_conn(db_path)
    try:
        try:
            updated = update_error_event_state(conn, error_id, "resolved")
        except RecordNotFoundError as exc:
            msg = str(exc)
            if as_json:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if as_json:
            typer.echo(json.dumps(updated, indent=2, ensure_ascii=False))
        else:
            typer.secho(f"✓ Error {error_id[:8]}... marked as resolved", fg=typer.colors.GREEN)
    finally:
        conn.close()


# =====================================================================
# Plan Commands
# =====================================================================


@plan_app.command("show")
def plan_show(
    version: Annotated[
        int | None,
        typer.Option("--version", "-v", help="Plan version number."),
    ] = None,
    plan_id: Annotated[
        str | None,
        typer.Option("--plan-id", help="Plan ID."),
    ] = None,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active learner)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output plan as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Display active plan or a specific plan version and its items."""
    conn = _get_conn(db_path)
    try:
        target_learner = learner_id
        if not target_learner:
            active = get_active_learner_profile(conn)
            if active:
                target_learner = active["learner_id"]
            else:
                if as_json:
                    typer.echo(json.dumps({"error": "No learner profile found"}, ensure_ascii=False))
                else:
                    typer.secho("No learner profile found.", fg=typer.colors.YELLOW)
                return

        if plan_id:
            plan = get_plan_version(conn, plan_id)
        elif version is not None:
            versions = list_plan_versions(conn, target_learner)
            plan_match = next((p for p in versions if p["version"] == version), None)
            if plan_match:
                plan = get_plan_version(conn, plan_match["plan_id"])
            else:
                plan = None
        else:
            plan = get_active_plan(conn, target_learner)

        if not plan:
            if as_json:
                typer.echo(json.dumps(None))
            else:
                typer.secho(f"No plan found for learner {target_learner}.", fg=typer.colors.YELLOW)
            return

        adherence = get_plan_adherence(conn, target_learner, plan["plan_id"])
        plan["adherence"] = adherence

        if as_json:
            typer.echo(json.dumps(plan, indent=2, ensure_ascii=False))
        else:
            typer.secho(f"=== Study Plan Version {plan['version']} ===", fg=typer.colors.CYAN, bold=True)
            typer.echo(f"Plan ID:     {plan['plan_id']}")
            typer.echo(f"Learner ID:  {plan['learner_id']}")
            typer.echo(f"Valid From:  {plan['valid_from']}")
            valid_to = plan.get("valid_to") or "Active (Open)"
            typer.echo(f"Valid To:    {valid_to}")
            if plan.get("created_by"):
                typer.echo(f"Created By:  {plan['created_by']}")
            if plan.get("rationale_md"):
                typer.echo(f"Rationale:   {plan['rationale_md']}")

            priorities = plan.get("primary_priorities_json")
            if priorities:
                typer.secho("\nPrimary Priorities:", fg=typer.colors.BRIGHT_WHITE, bold=True)
                if isinstance(priorities, str):
                    try:
                        priorities = json.loads(priorities)
                    except Exception:
                        pass
                if isinstance(priorities, list):
                    for p in priorities:
                        typer.echo(f"  • {p}")
                else:
                    typer.echo(f"  • {priorities}")

            items = plan.get("items", [])
            rate_pct = adherence['adherence_rate'] * 100
            typer.secho(f"\nPlan Items ({len(items)} items | Adherence: {rate_pct:.1f}%):", fg=typer.colors.BRIGHT_WHITE, bold=True)
            if not items:
                typer.echo("  (No planned items)")
            else:
                for it in items:
                    status_color = typer.colors.GREEN if it["status"] == "done" else (typer.colors.YELLOW if it["status"] == "in_progress" else typer.colors.WHITE)
                    status_badge = typer.style(f"[{it['status']}]", fg=status_color, bold=True)
                    due_str = f" due {it['due_date']}" if it.get("due_date") else ""
                    min_str = f" {it['target_minutes']}m" if it.get("target_minutes") else ""
                    cnt_str = f" {it['target_count']}x" if it.get("target_count") else ""
                    typer.echo(f"  - {it['plan_item_id'][:8]}... {status_badge} [{it['module']}] {it['activity_type']}{min_str}{cnt_str}{due_str}")
    finally:
        conn.close()


@plan_app.command("create")
def plan_create(
    json_input: Annotated[
        str | None,
        typer.Option("--json-input", "-j", help="Plan JSON payload string or @file.json."),
    ] = None,
    priorities: Annotated[
        str | None,
        typer.Option("--priorities", "-p", help="Comma-separated or JSON list of primary priorities."),
    ] = None,
    rationale: Annotated[
        str | None,
        typer.Option("--rationale", "-r", help="Rationale markdown explanation for this plan version."),
    ] = None,
    items: Annotated[
        str | None,
        typer.Option("--items", help="JSON array of plan items or @file.json."),
    ] = None,
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active learner)."),
    ] = None,
    created_by: Annotated[
        str | None,
        typer.Option("--created-by", help="Author of the plan snapshot (e.g. agent)."),
    ] = "agent",
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output created plan as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Create a new immutable plan version snapshot."""
    conn = _get_conn(db_path)
    try:
        t_learner = learner_id
        t_priorities = priorities
        t_rationale = rationale
        t_items: list[dict[str, Any]] = []
        t_created_by = created_by
        t_valid_from: str | None = None
        is_json_invoked = False

        if json_input:
            is_json_invoked = True
            payload = _parse_json_payload(json_input)
            t_learner = payload.get("learner_id") or t_learner
            t_priorities = payload.get("primary_priorities") or payload.get("primary_priorities_json") or t_priorities
            t_rationale = payload.get("rationale_md") or payload.get("rationale") or t_rationale
            t_created_by = payload.get("created_by") or t_created_by
            t_valid_from = payload.get("valid_from")
            if "items" in payload and isinstance(payload["items"], list):
                t_items = payload["items"]

        if items:
            parsed_items = _parse_json_payload(items)
            if isinstance(parsed_items, list):
                t_items = parsed_items

        if not t_learner:
            active = get_active_learner_profile(conn)
            if active:
                t_learner = active["learner_id"]
            else:
                msg = "No learner profile found. Create one first with 'cetpm profile update'."
                if as_json or is_json_invoked:
                    typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
                else:
                    typer.secho(msg, fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)

        if not t_priorities:
            msg = "Missing primary priorities. Provide --priorities or JSON payload."
            if as_json or is_json_invoked:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        if isinstance(t_priorities, str):
            trimmed = t_priorities.strip()
            if (trimmed.startswith("[") and trimmed.endswith("]")) or (trimmed.startswith("{") and trimmed.endswith("}")):
                try:
                    t_priorities = json.loads(trimmed)
                except Exception:
                    pass
            elif "," in trimmed:
                t_priorities = [p.strip() for p in trimmed.split(",") if p.strip()]
            else:
                t_priorities = [trimmed]

        if not t_rationale or not str(t_rationale).strip():
            msg = "A change rationale (--rationale) is mandatory when creating or updating a plan version."
            if as_json or is_json_invoked:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        try:
            validated_items = []
            for item in t_items:
                model_item = PlanItemModel(**item)
                validated_items.append(model_item.model_dump(exclude_none=True))

            created = create_plan_version(
                conn,
                learner_id=t_learner,
                valid_from=t_valid_from,
                primary_priorities=t_priorities,
                rationale_md=t_rationale,
                created_by=t_created_by,
                items=validated_items,
            )
        except (ValidationError, ValueError) as exc:
            msg = str(exc)
            if as_json or is_json_invoked:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(f"Validation error: {msg}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if as_json:
            typer.echo(json.dumps(created, indent=2, ensure_ascii=False))
        else:
            typer.secho(f"✓ Plan Version {created['version']} Created", fg=typer.colors.GREEN, bold=True)
            typer.echo(f"Plan ID:     {created['plan_id']}")
            typer.echo(f"Learner ID:  {created['learner_id']}")
            typer.echo(f"Valid From:  {created['valid_from']}")
            typer.echo(f"Rationale:   {created.get('rationale_md')}")
            typer.echo(f"Total Items: {len(created.get('items', []))}")
    finally:
        conn.close()


@plan_app.command("list")
def plan_list(
    learner_id: Annotated[
        str | None,
        typer.Option("--learner-id", help="Learner ID (defaults to active learner)."),
    ] = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output plan versions as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """List all plan versions for a learner."""
    conn = _get_conn(db_path)
    try:
        target_learner = learner_id
        if not target_learner:
            active = get_active_learner_profile(conn)
            if active:
                target_learner = active["learner_id"]
            else:
                if as_json:
                    typer.echo("[]")
                else:
                    typer.secho("No learner profile found.", fg=typer.colors.YELLOW)
                return

        versions = list_plan_versions(conn, target_learner)
        if as_json:
            typer.echo(json.dumps(versions, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Total Plan Versions: {len(versions)}")
            for v in versions:
                valid_to = v.get("valid_to") or "Active"
                rat = v.get("rationale_md") or ""
                rat_preview = f" | {rat[:40]}..." if len(rat) > 40 else (f" | {rat}" if rat else "")
                typer.echo(f"- v{v['version']} ({v['plan_id'][:8]}...) [{v['valid_from'][:10]} ~ {valid_to[:10]}]{rat_preview}")
    finally:
        conn.close()


@plan_app.command("update-item")
def plan_update_item(
    plan_item_id: Annotated[
        str,
        typer.Argument(help="Plan item ID to update."),
    ],
    status: Annotated[
        str,
        typer.Option("--status", "-s", help="New status: planned, in_progress, done, skipped."),
    ],
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Output updated plan item as JSON."),
    ] = False,
    db_path: Annotated[
        str | None,
        typer.Option("--db-path", help="Path to SQLite database file."),
    ] = None,
) -> None:
    """Update status of a specific plan item."""
    conn = _get_conn(db_path)
    try:
        try:
            updated = update_plan_item_status(conn, plan_item_id, status)
        except RecordNotFoundError as exc:
            msg = str(exc)
            if as_json:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(msg, fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc
        except (ValidationError, ValueError) as exc:
            msg = str(exc)
            if as_json:
                typer.echo(json.dumps({"error": msg}, ensure_ascii=False))
            else:
                typer.secho(f"Validation error: {msg}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from exc

        if as_json:
            typer.echo(json.dumps(updated, indent=2, ensure_ascii=False))
        else:
            typer.secho(f"✓ Plan item {plan_item_id[:8]}... status updated to '{status}'", fg=typer.colors.GREEN)
    finally:
        conn.close()


def main() -> None:
    """Run the CLI application."""
    app()
