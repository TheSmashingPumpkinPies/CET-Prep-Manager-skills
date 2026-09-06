"""Deterministic Markdown reporting for CET Prep Manager."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Any

from cet_prep_manager.analytics import build_learner_state_snapshot, get_intervention_response
from cet_prep_manager.db import (
    RecordNotFoundError,
    get_learner_profile,
    get_plan_adherence,
    list_error_events,
    list_exam_attempts,
    list_training_sessions,
)


def _as_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _in_window(value: str, start: datetime, end: datetime) -> bool:
    observed = _as_utc(value)
    return start <= observed <= end


def build_weekly_report_data(
    conn: sqlite3.Connection,
    learner_id: str,
    *,
    days: int = 7,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Build the observed and derived data used by a weekly Markdown report."""
    if days < 1:
        raise ValueError("days must be at least 1")
    profile = get_learner_profile(conn, learner_id)
    if profile is None:
        raise RecordNotFoundError(f"Learner profile '{learner_id}' not found")

    period_end = as_of or datetime.now(timezone.utc)
    if period_end.tzinfo is None:
        period_end = period_end.replace(tzinfo=timezone.utc)
    period_end = period_end.astimezone(timezone.utc)
    period_start = period_end - timedelta(days=days)

    attempts = [
        row
        for row in list_exam_attempts(conn, learner_id, limit=1000)
        if _in_window(row["attempted_at"], period_start, period_end)
    ]
    sessions = [
        row
        for row in list_training_sessions(conn, learner_id, limit=1000)
        if _in_window(row["started_at"], period_start, period_end)
    ]
    errors = [
        row
        for row in list_error_events(conn, learner_id)
        if _in_window(row["created_at"], period_start, period_end)
    ]
    official_results = [
        {
            "attempt_id": row["attempt_id"],
            "exam_level": row["exam_level"],
            "attempted_at": row["attempted_at"],
            "official_reported_score": row["official_reported_score"],
        }
        for row in attempts
        if row.get("official_reported_score") is not None
    ]

    completed_sessions = [row for row in sessions if bool(row.get("completed"))]
    training_seconds = sum(int(row.get("duration_seconds") or 0) for row in sessions)
    snapshot = build_learner_state_snapshot(conn, learner_id)
    interventions = [
        get_intervention_response(conn, learner_id, row["session_id"])
        for row in sessions
        if row.get("module") in {"listening", "reading", "writing", "translation"}
    ]

    return {
        "learner_id": learner_id,
        "display_name": profile.get("display_name"),
        "target_exam": profile["target_exam"],
        "period": {
            "days": days,
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "observed": {
            "attempt_count": len(attempts),
            "training_session_count": len(sessions),
            "completed_training_session_count": len(completed_sessions),
            "training_minutes": round(training_seconds / 60, 1),
            "new_error_count": len(errors),
            "official_results": official_results,
        },
        "derived": {
            "modules": {
                name: state.model_dump(mode="json")
                for name, state in snapshot.modules.items()
            },
            "module_balance": snapshot.module_balance.model_dump(mode="json"),
            "top_error_codes": snapshot.top_error_codes,
            "plan_adherence": get_plan_adherence(conn, learner_id),
            "interventions": interventions,
        },
    }


def _format_metric(module: str, value: float | None) -> str:
    if value is None:
        return "—"
    if module in {"writing", "translation"}:
        return f"{value:.2f}/15"
    return f"{value * 100:.1f}%"


def render_weekly_report(data: dict[str, Any]) -> str:
    """Render deterministic weekly data as Markdown with explicit evidence classes."""
    observed = data["observed"]
    derived = data["derived"]
    period = data["period"]
    title_name = data.get("display_name") or data["learner_id"]
    lines = [
        f"# CET Weekly Report — {title_name}",
        "",
        f"Period: `{period['start']}` to `{period['end']}` ({period['days']} days)",
        "",
        "## Observed",
        "",
        f"- Attempts recorded: {observed['attempt_count']}",
        f"- Training sessions: {observed['training_session_count']} "
        f"({observed['completed_training_session_count']} completed)",
        f"- Training time: {observed['training_minutes']:.1f} minutes",
        f"- New error events: {observed['new_error_count']}",
    ]
    if observed["official_results"]:
        lines.extend(["", "### Actual official CET results", ""])
        for result in observed["official_results"]:
            lines.append(
                f"- {result['exam_level']} `{result['attempted_at']}`: "
                f"{result['official_reported_score']}/710 "
                f"(attempt `{result['attempt_id']}`)"
            )

    lines.extend(
        [
            "",
            "## Derived",
            "",
            "| Module | N | Latest | MA3 | MA5 | Slope | Sufficiency |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for module in ("listening", "reading", "writing", "translation"):
        state = derived["modules"][module]
        slope = "—" if state["trend_slope"] is None else f"{state['trend_slope']:+.4f}"
        lines.append(
            f"| {module} | {state['n']} | {_format_metric(module, state['latest'])} | "
            f"{_format_metric(module, state['ma3'])} | {_format_metric(module, state['ma5'])} | "
            f"{slope} | {state['sufficiency']} |"
        )

    balance = derived["module_balance"]
    adherence = derived["plan_adherence"]
    lines.extend(["", "### Planning signals", ""])
    if balance["gap"] is None:
        lines.append("- Module balance: insufficient comparable module observations.")
    else:
        lines.append(
            f"- Module balance (diagnostic only): strongest `{balance['strongest_module']}`, "
            f"weakest `{balance['weakest_module']}`, normalized gap {balance['gap']:.4f}."
        )
    lines.append(
        f"- Active-plan adherence: {adherence['done']}/{adherence['total_items']} done "
        f"({adherence['adherence_rate'] * 100:.1f}%)."
    )
    if derived["top_error_codes"]:
        lines.append("- Persistent error codes: " + ", ".join(derived["top_error_codes"]) + ".")

    supported = [item for item in derived["interventions"] if item.get("supported")]
    if supported:
        lines.extend(["", "### Exploratory intervention checks", ""])
        for item in supported:
            if item["sufficient"]:
                lines.append(
                    f"- `{item['session_id']}` ({item['module']}): {item['direction']}, "
                    f"delta {item['delta']:+.4f} {item['unit']} "
                    f"(2 observations before vs 2 after)."
                )
            else:
                lines.append(
                    f"- `{item['session_id']}` ({item['module']}): insufficient data "
                    f"({item['pre_n']} before, {item['post_n']} after; 2 each required)."
                )

    lines.extend(
        [
            "",
            "## AI interpretation boundary",
            "",
            "This file contains observed records and deterministic derivations. An agent may add "
            "coaching interpretation only after loading the same learner state, citing sample "
            "sizes and preserving the distinction between practice metrics, AI estimates, and "
            "actual official results.",
            "",
        ]
    )
    return "\n".join(lines)


def write_weekly_report(
    conn: sqlite3.Connection,
    learner_id: str,
    output_path: Path | str,
    *,
    days: int = 7,
    as_of: datetime | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Build and write a weekly Markdown report without mutating learner history."""
    data = build_weekly_report_data(conn, learner_id, days=days, as_of=as_of)
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_weekly_report(data), encoding="utf-8")
    return target, data
