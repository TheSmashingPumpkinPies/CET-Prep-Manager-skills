"""Deterministic analytics engine for CET Prep Manager."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import sqlite3
from typing import Any

from cet_prep_manager.db import (
    RecordNotFoundError,
    get_active_plan,
    get_learner_profile,
    get_training_session,
    list_error_events,
    list_section_results,
    list_subjective_assessments,
)
from cet_prep_manager.models import (
    DataSufficiency,
    LearnerStateSnapshot,
    ModuleBalance,
    ModuleState,
)


def compute_moving_average(values: list[float], window: int) -> float | None:
    """Compute moving average over the last `window` values.

    Returns None if fewer than `window` observations exist.
    """
    if len(values) < window:
        return None
    return round(sum(values[-window:]) / window, 4)


def compute_least_squares_slope(values: list[float], max_window: int = 5) -> float | None:
    """Compute least-squares trend slope over recent observations (up to max_window).

    Returns None if sample size is less than 3, per product spec.
    """
    if len(values) < 3:
        return None
    k = min(len(values), max_window)
    y = values[-k:]
    x = list(range(k))
    x_mean = sum(x) / k
    y_mean = sum(y) / k
    numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(k))
    denominator = sum((x[i] - x_mean) ** 2 for i in range(k))
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def compute_volatility(values: list[float], max_window: int = 5) -> float | None:
    """Compute sample standard deviation over recent observations (up to max_window).

    Returns None if sample size is less than 2.
    """
    if len(values) < 2:
        return None
    k = min(len(values), max_window)
    subset = values[-k:]
    mean = sum(subset) / k
    variance = sum((val - mean) ** 2 for val in subset) / (k - 1)
    return round(math.sqrt(variance), 4)


def compute_data_sufficiency(n: int) -> DataSufficiency:
    """Determine data sufficiency classification:

    - insufficient: n < 3
    - emerging: 3 <= n <= 4
    - usable: n >= 5
    """
    if n < 3:
        return DataSufficiency.INSUFFICIENT
    elif n <= 4:
        return DataSufficiency.EMERGING
    else:
        return DataSufficiency.USABLE


def compute_module_state(values: list[float]) -> ModuleState:
    """Compute ModuleState summary from an ordered series of numeric values."""
    n = len(values)
    if n == 0:
        return ModuleState(
            latest=None,
            ma3=None,
            ma5=None,
            trend_slope=None,
            volatility=None,
            n=0,
            sufficiency=DataSufficiency.INSUFFICIENT,
        )

    latest = round(values[-1], 4)
    ma3 = compute_moving_average(values, 3)
    ma5 = compute_moving_average(values, 5)
    trend_slope = compute_least_squares_slope(values, max_window=5)
    volatility = compute_volatility(values, max_window=5)
    sufficiency = compute_data_sufficiency(n)

    return ModuleState(
        latest=latest,
        ma3=ma3,
        ma5=ma5,
        trend_slope=trend_slope,
        volatility=volatility,
        n=n,
        sufficiency=sufficiency,
    )


def compute_module_balance(modules: dict[str, ModuleState]) -> ModuleBalance:
    """Compare latest module observations on a diagnostic 0–1 scale.

    Objective sections already use accuracy. Writing and translation scores are divided by 15.
    The result is a planning aid, not an official cross-section score conversion.
    """
    normalized: dict[str, float] = {}
    for name in ("listening", "reading", "writing", "translation"):
        state = modules.get(name)
        if state is None or state.latest is None:
            continue
        value = state.latest / 15 if name in {"writing", "translation"} else state.latest
        normalized[name] = round(max(0.0, min(1.0, value)), 4)

    if not normalized:
        return ModuleBalance()

    ordered = sorted(normalized)
    strongest = max(ordered, key=lambda name: normalized[name])
    weakest = min(ordered, key=lambda name: normalized[name])
    values = list(normalized.values())
    gap = round(max(values) - min(values), 4) if len(values) >= 2 else None
    return ModuleBalance(
        normalized_latest=normalized,
        available_modules=len(values),
        mean=round(sum(values) / len(values), 4),
        gap=gap,
        strongest_module=strongest,
        weakest_module=weakest,
        is_balanced=(gap <= 0.1) if gap is not None else None,
    )


def get_objective_section_series(
    conn: sqlite3.Connection,
    learner_id: str,
    section: str,
) -> list[float]:
    """Retrieve chronological accuracy series (0.0–1.0) for an objective section."""
    rows = list_section_results(conn, learner_id, section=section)
    series: list[float] = []
    for r in rows:
        acc = r.get("accuracy")
        if acc is not None:
            series.append(float(acc))
        elif r.get("correct_count") is not None and r.get("total_count") is not None and r["total_count"] > 0:
            series.append(round(r["correct_count"] / r["total_count"], 4))
    return series


def get_subjective_section_series(
    conn: sqlite3.Connection,
    learner_id: str,
    section: str,
) -> list[float]:
    """Retrieve chronological estimated score series (1–15) for a subjective section."""
    rows = list_subjective_assessments(conn, learner_id, section=section, limit=1000)
    return [float(r["estimated_score"]) for r in rows if r.get("estimated_score") is not None]


def get_top_error_codes(
    conn: sqlite3.Connection,
    learner_id: str,
    limit: int = 5,
) -> list[str]:
    """Extract top error taxonomy codes for a learner, weighted by recurrence and unresolved state."""
    errors = list_error_events(conn, learner_id)
    if not errors:
        return []

    counts: dict[str, int] = {}
    for err in errors:
        code = err.get("taxonomy_code")
        if not code:
            continue
        state = err.get("resolved_state", "new")
        weight = 2 if state in ("new", "recurrent") else (1 if state == "improving" else 0)
        counts[code] = counts.get(code, 0) + 1 + weight

    sorted_codes = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [code for code, _ in sorted_codes[:limit]]


def build_learner_state_snapshot(
    conn: sqlite3.Connection,
    learner_id: str,
) -> LearnerStateSnapshot:
    """Build a comprehensive LearnerStateSnapshot from SQLite database."""
    profile = get_learner_profile(conn, learner_id)
    if not profile:
        raise RecordNotFoundError(f"Learner profile '{learner_id}' not found")

    listening_series = get_objective_section_series(conn, learner_id, "listening")
    reading_series = get_objective_section_series(conn, learner_id, "reading")
    writing_series = get_subjective_section_series(conn, learner_id, "writing")
    translation_series = get_subjective_section_series(conn, learner_id, "translation")

    modules = {
        "listening": compute_module_state(listening_series),
        "reading": compute_module_state(reading_series),
        "writing": compute_module_state(writing_series),
        "translation": compute_module_state(translation_series),
    }

    top_errors = get_top_error_codes(conn, learner_id, limit=5)
    active_plan = get_active_plan(conn, learner_id)
    active_plan_id = active_plan["plan_id"] if active_plan else None
    module_balance = compute_module_balance(modules)

    return LearnerStateSnapshot(
        learner_id=profile["learner_id"],
        target_exam=profile["target_exam"],
        target_exam_date=profile.get("target_exam_date"),
        target_reported_score=profile.get("target_reported_score"),
        generated_at=datetime.now(timezone.utc).isoformat(),
        modules=modules,
        module_balance=module_balance,
        top_error_codes=top_errors,
        active_plan_id=active_plan_id,
    )


def compute_intervention_response(
    before: list[float],
    after: list[float],
    *,
    min_observations: int = 2,
) -> dict[str, Any]:
    """Compare closest pre/post observations for an exploratory intervention result."""
    if min_observations < 1:
        raise ValueError("min_observations must be at least 1")

    pre_window = before[-min_observations:]
    post_window = after[:min_observations]
    pre_mean = round(sum(pre_window) / len(pre_window), 4) if pre_window else None
    post_mean = round(sum(post_window) / len(post_window), 4) if post_window else None
    sufficient = (
        len(pre_window) >= min_observations
        and len(post_window) >= min_observations
    )
    delta = (
        round(post_mean - pre_mean, 4)
        if sufficient and pre_mean is not None and post_mean is not None
        else None
    )
    direction = None
    if delta is not None:
        direction = "improved" if delta > 0 else "declined" if delta < 0 else "unchanged"

    return {
        "exploratory": True,
        "sufficient": sufficient,
        "required_per_window": min_observations,
        "pre_n": len(pre_window),
        "post_n": len(post_window),
        "pre_values": [round(value, 4) for value in pre_window],
        "post_values": [round(value, 4) for value in post_window],
        "pre_mean": pre_mean,
        "post_mean": post_mean,
        "delta": delta,
        "direction": direction,
    }


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_intervention_response(
    conn: sqlite3.Connection,
    learner_id: str,
    session_id: str,
) -> dict[str, Any]:
    """Calculate exploratory response around one identifiable training session."""
    session = get_training_session(conn, session_id)
    if session is None or session["learner_id"] != learner_id:
        raise RecordNotFoundError(
            f"Training session '{session_id}' was not found for learner '{learner_id}'"
        )

    module = session.get("module")
    if module in {"listening", "reading"}:
        rows = conn.execute(
            """
            SELECT a.attempted_at AS observed_at,
                   COALESCE(
                       sr.accuracy,
                       CAST(sr.correct_count AS REAL) / NULLIF(sr.total_count, 0)
                   ) AS value
            FROM section_result AS sr
            JOIN exam_attempt AS a ON a.attempt_id = sr.attempt_id
            WHERE a.learner_id = ? AND sr.section = ?
            ORDER BY a.attempted_at ASC, sr.section_result_id ASC;
            """,
            (learner_id, module),
        ).fetchall()
        unit = "accuracy"
    elif module in {"writing", "translation"}:
        rows = conn.execute(
            """
            SELECT created_at AS observed_at, estimated_score AS value
            FROM subjective_assessment
            WHERE learner_id = ? AND section = ?
            ORDER BY created_at ASC, assessment_id ASC;
            """,
            (learner_id, module),
        ).fetchall()
        unit = "score_1_to_15"
    else:
        return {
            "session_id": session_id,
            "module": module,
            "activity_type": session.get("activity_type"),
            "started_at": session["started_at"],
            "unit": None,
            "supported": False,
            "exploratory": True,
            "sufficient": False,
            "reason": "No comparable outcome metric is defined for this training module.",
        }

    intervention_at = _parse_timestamp(session["started_at"])
    observations = [
        (_parse_timestamp(row["observed_at"]), float(row["value"]))
        for row in rows
        if row["value"] is not None
    ]
    before = [value for observed_at, value in observations if observed_at < intervention_at]
    after = [value for observed_at, value in observations if observed_at > intervention_at]
    response = compute_intervention_response(before, after)
    return {
        "session_id": session_id,
        "module": module,
        "activity_type": session.get("activity_type"),
        "started_at": session["started_at"],
        "unit": unit,
        "supported": True,
        **response,
    }


def compute_subjective_uncertainty_trend(
    assessments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute uncertainty-aware trend metrics for subjective assessments (AN-003).

    Calculates numeric confidence intervals, overlap ratio against prior assessment/baseline,
    statistical distinction, and interval width trend (convergence/divergence) without
    introducing LLM prose into the analytics module.
    """
    n = len(assessments)
    if n == 0:
        return {
            "sample_size": 0,
            "sufficiency": DataSufficiency.INSUFFICIENT.value,
            "latest_score": None,
            "latest_range": None,
            "baseline_score": None,
            "baseline_range": None,
            "score_delta": None,
            "intervals_overlap": None,
            "interval_overlap_ratio": None,
            "is_statistically_distinct": False,
            "mean_interval_width": None,
            "interval_width_trend": None,
        }

    latest = assessments[-1]
    latest_score = float(latest["estimated_score"])
    latest_low = float(latest["score_low"])
    latest_high = float(latest["score_high"])
    latest_range = [latest_low, latest_high]

    baseline = assessments[0]
    baseline_score = float(baseline["estimated_score"])
    baseline_low = float(baseline["score_low"])
    baseline_high = float(baseline["score_high"])
    baseline_range = [baseline_low, baseline_high]

    score_delta = round(latest_score - baseline_score, 2)

    comparator = assessments[-2] if n >= 2 else baseline
    comp_low = float(comparator["score_low"])
    comp_high = float(comparator["score_high"])

    overlap_start = max(latest_low, comp_low)
    overlap_end = min(latest_high, comp_high)
    overlap_len = max(0.0, overlap_end - overlap_start)

    min_span = min(latest_high - latest_low, comp_high - comp_low)
    overlap_ratio = round(overlap_len / min_span, 3) if min_span > 0 else 0.0
    intervals_overlap = overlap_len > 0
    is_statistically_distinct = (latest_low > comp_high) or (latest_high < comp_low)

    widths = [float(a["score_high"]) - float(a["score_low"]) for a in assessments]
    mean_width = round(sum(widths) / len(widths), 2)

    width_trend = "stable"
    if n >= 3:
        w_slope = compute_least_squares_slope(widths)
        if w_slope is not None:
            if w_slope < -0.05:
                width_trend = "converging"
            elif w_slope > 0.05:
                width_trend = "diverging"
            else:
                width_trend = "stable"

    return {
        "sample_size": n,
        "sufficiency": compute_data_sufficiency(n).value,
        "latest_score": latest_score,
        "latest_range": latest_range,
        "baseline_score": baseline_score,
        "baseline_range": baseline_range,
        "score_delta": score_delta,
        "intervals_overlap": intervals_overlap,
        "interval_overlap_ratio": overlap_ratio,
        "is_statistically_distinct": is_statistically_distinct,
        "mean_interval_width": mean_width,
        "interval_width_trend": width_trend,
    }


def get_subjective_uncertainty_summary(
    conn: sqlite3.Connection,
    learner_id: str,
    section: str,
) -> dict[str, Any]:
    """Retrieve all subjective assessments for a section and compute uncertainty trend."""
    assessments = list_subjective_assessments(conn, learner_id, section=section, limit=1000)
    return compute_subjective_uncertainty_trend(assessments)

