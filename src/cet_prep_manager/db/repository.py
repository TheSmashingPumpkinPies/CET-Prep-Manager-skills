"""Repository and data access layer for CET Prep Manager (DB-002)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any
import uuid

from cet_prep_manager.db.exceptions import (
    DuplicateIdempotencyKeyError,
    RecordNotFoundError,
    ValidationError,
)

VALID_EXAM_LEVELS = {"CET4", "CET6"}
VALID_ATTEMPT_TYPES = {"full_mock", "section_practice", "official_exam", "diagnostic"}
VALID_SOURCE_KINDS = {"past_paper", "commercial_mock", "generated", "official_result", "other"}
VALID_SECTIONS = {"listening", "reading", "writing", "translation", "overall"}
VALID_SUBJECTIVE_SECTIONS = {"writing", "translation"}
VALID_CONFIDENCES = {"low", "medium", "high"}
VALID_ANCHOR_BANDS = {14, 11, 8, 5, 2}
VALID_BAND_LOWS = {1, 4, 7, 10, 13}
VALID_BAND_HIGHS = {3, 6, 9, 12, 15}
VALID_ERROR_RESOLVED_STATES = {"new", "recurrent", "improving", "resolved"}
VALID_PLAN_ITEM_STATUSES = {"planned", "in_progress", "done", "skipped"}
VALID_TRAINING_MODULES = {"listening", "reading", "writing", "translation", "vocabulary", "full_mock"}


def ensure_valid_json(val: str | dict | list, field_name: str) -> str:
    """Validate and serialize JSON data."""
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    if isinstance(val, str):
        try:
            json.loads(val)
            return val
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Corrupted or invalid JSON for {field_name}: {exc}") from exc
    raise ValidationError(f"Expected dict, list, or JSON string for {field_name}, got {type(val).__name__}")


def _infer_band_info(score: float) -> tuple[int, int, int]:
    if score >= 13:
        return 14, 13, 15
    elif score >= 10:
        return 11, 10, 12
    elif score >= 7:
        return 8, 7, 9
    elif score >= 4:
        return 5, 4, 6
    else:
        return 2, 1, 3


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


# =====================================================================
# Learner Profile
# =====================================================================


def get_learner_profile(conn: sqlite3.Connection, learner_id: str) -> dict[str, Any] | None:
    """Get a learner profile by ID."""
    cursor = conn.execute(
        "SELECT * FROM learner_profile WHERE learner_id = ?;",
        (learner_id,),
    )
    return _row_to_dict(cursor.fetchone())


def get_active_learner_profile(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Get the active learner profile (the most recently updated)."""
    cursor = conn.execute(
        "SELECT * FROM learner_profile ORDER BY updated_at DESC LIMIT 1;"
    )
    return _row_to_dict(cursor.fetchone())


def list_learner_profiles(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """List all learner profiles."""
    cursor = conn.execute(
        "SELECT * FROM learner_profile ORDER BY updated_at DESC;"
    )
    return [dict(row) for row in cursor.fetchall()]


def create_or_update_learner_profile(
    conn: sqlite3.Connection,
    *,
    learner_id: str | None = None,
    display_name: str | None = None,
    target_exam: str,
    target_exam_date: str | None = None,
    target_reported_score: int | None = None,
    daily_minutes: int | None = None,
) -> dict[str, Any]:
    """Create a new learner profile or update an existing one."""
    if target_exam not in VALID_EXAM_LEVELS:
        raise ValidationError(f"Invalid target_exam '{target_exam}', must be CET4 or CET6")

    if daily_minutes is not None and daily_minutes < 0:
        raise ValidationError("daily_minutes cannot be negative")

    now = datetime.now(timezone.utc).isoformat()

    with conn:
        target_id = learner_id
        if target_id is None:
            active = get_active_learner_profile(conn)
            if active:
                target_id = active["learner_id"]

        if target_id is not None:
            existing = get_learner_profile(conn, target_id)
            if existing:
                conn.execute(
                    """
                    UPDATE learner_profile
                    SET display_name = coalesce(?, display_name),
                        target_exam = ?,
                        target_exam_date = coalesce(?, target_exam_date),
                        target_reported_score = coalesce(?, target_reported_score),
                        daily_minutes = coalesce(?, daily_minutes),
                        updated_at = ?
                    WHERE learner_id = ?;
                    """,
                    (
                        display_name,
                        target_exam,
                        target_exam_date,
                        target_reported_score,
                        daily_minutes,
                        now,
                        target_id,
                    ),
                )
                profile = get_learner_profile(conn, target_id)
                assert profile is not None
                return profile

        new_id = target_id or str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO learner_profile (
                learner_id, display_name, target_exam, target_exam_date,
                target_reported_score, daily_minutes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                new_id,
                display_name,
                target_exam,
                target_exam_date,
                target_reported_score,
                daily_minutes,
                now,
                now,
            ),
        )
        profile = get_learner_profile(conn, new_id)
        assert profile is not None
        return profile


# =====================================================================
# Exam Attempt
# =====================================================================


def find_attempt_by_idempotency_key(
    conn: sqlite3.Connection,
    idempotency_key: str,
) -> dict[str, Any] | None:
    """Find an exam attempt by its idempotency key."""
    cursor = conn.execute(
        "SELECT * FROM exam_attempt WHERE idempotency_key = ?;",
        (idempotency_key,),
    )
    return _row_to_dict(cursor.fetchone())


def get_exam_attempt(
    conn: sqlite3.Connection,
    attempt_id: str,
    *,
    include_details: bool = True,
) -> dict[str, Any] | None:
    """Retrieve an exam attempt with its nested sections, assessments, and errors."""
    cursor = conn.execute(
        "SELECT * FROM exam_attempt WHERE attempt_id = ?;",
        (attempt_id,),
    )
    attempt = _row_to_dict(cursor.fetchone())
    if attempt is None:
        return None

    if include_details:
        sec_cursor = conn.execute(
            "SELECT * FROM section_result WHERE attempt_id = ? ORDER BY section ASC;",
            (attempt_id,),
        )
        attempt["section_results"] = [dict(r) for r in sec_cursor.fetchall()]

        sub_cursor = conn.execute(
            "SELECT * FROM subjective_assessment WHERE attempt_id = ? ORDER BY section ASC;",
            (attempt_id,),
        )
        attempt["subjective_assessments"] = [dict(r) for r in sub_cursor.fetchall()]

        err_cursor = conn.execute(
            "SELECT * FROM error_event WHERE attempt_id = ? ORDER BY created_at ASC;",
            (attempt_id,),
        )
        attempt["error_events"] = [dict(r) for r in err_cursor.fetchall()]

    return attempt


def list_exam_attempts(
    conn: sqlite3.Connection,
    learner_id: str,
    *,
    exam_level: str | None = None,
    attempt_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List exam attempts for a learner with optional filtering."""
    query = "SELECT * FROM exam_attempt WHERE learner_id = ?"
    params: list[Any] = [learner_id]

    if exam_level:
        query += " AND exam_level = ?"
        params.append(exam_level)
    if attempt_type:
        query += " AND attempt_type = ?"
        params.append(attempt_type)

    query += " ORDER BY attempted_at DESC, created_at DESC LIMIT ? OFFSET ?;"
    params.extend([limit, offset])

    cursor = conn.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def create_exam_attempt(
    conn: sqlite3.Connection,
    *,
    attempt_id: str | None = None,
    learner_id: str,
    attempted_at: str | None = None,
    exam_level: str,
    attempt_type: str,
    source_key: str | None = None,
    source_kind: str | None = None,
    official_reported_score: int | None = None,
    duration_seconds: int | None = None,
    notes: str | None = None,
    idempotency_key: str | None = None,
    section_results: list[dict[str, Any]] | None = None,
    subjective_assessments: list[dict[str, Any]] | None = None,
    error_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create an exam attempt and its optional child records in a single transaction.

    Enforces idempotency and rejects duplicate idempotency keys.
    """
    if exam_level not in VALID_EXAM_LEVELS:
        raise ValidationError(f"Invalid exam_level '{exam_level}', must be CET4 or CET6")

    if attempt_type not in VALID_ATTEMPT_TYPES:
        raise ValidationError(f"Invalid attempt_type '{attempt_type}', must be one of {VALID_ATTEMPT_TYPES}")

    if source_kind is not None and source_kind not in VALID_SOURCE_KINDS:
        raise ValidationError(f"Invalid source_kind '{source_kind}', must be one of {VALID_SOURCE_KINDS}")

    if official_reported_score is not None:
        if attempt_type != "official_exam":
            raise ValidationError(
                "official_reported_score may only be stored for attempt_type='official_exam'"
            )
        if not 0 <= official_reported_score <= 710:
            raise ValidationError("official_reported_score must be between 0 and 710")

    if duration_seconds is not None and duration_seconds < 0:
        raise ValidationError("duration_seconds cannot be negative")

    # Idempotency check: duplicate keys must be rejected
    if idempotency_key:
        existing = find_attempt_by_idempotency_key(conn, idempotency_key)
        if existing:
            raise DuplicateIdempotencyKeyError(idempotency_key, existing["attempt_id"])

    # Ensure learner exists
    if get_learner_profile(conn, learner_id) is None:
        raise RecordNotFoundError(f"Learner '{learner_id}' not found")

    new_attempt_id = attempt_id or str(uuid.uuid4())
    attempt_timestamp = attempted_at or datetime.now(timezone.utc).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    with conn:
        try:
            conn.execute(
                """
                INSERT INTO exam_attempt (
                    attempt_id, learner_id, attempted_at, exam_level,
                    attempt_type, source_key, source_kind, official_reported_score,
                    duration_seconds, notes, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    new_attempt_id,
                    learner_id,
                    attempt_timestamp,
                    exam_level,
                    attempt_type,
                    source_key,
                    source_kind,
                    official_reported_score,
                    duration_seconds,
                    notes,
                    idempotency_key,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "idempotency_key" in str(exc).lower():
                existing = find_attempt_by_idempotency_key(conn, idempotency_key or "")
                existing_id = existing["attempt_id"] if existing else None
                raise DuplicateIdempotencyKeyError(idempotency_key or "", existing_id) from exc
            raise

        if section_results:
            for s in section_results:
                add_section_result(
                    conn,
                    attempt_id=new_attempt_id,
                    section=s["section"],
                    subtype=s.get("subtype"),
                    correct_count=s.get("correct_count"),
                    total_count=s.get("total_count"),
                    accuracy=s.get("accuracy"),
                    duration_seconds=s.get("duration_seconds"),
                    practice_index=s.get("practice_index"),
                    provenance=s.get("provenance"),
                )

        if subjective_assessments:
            for raw_sub in subjective_assessments:
                sub = dict(raw_sub)
                est = float(sub["estimated_score"])
                exp_anchor, exp_low, exp_high = _infer_band_info(est)
                anchor = sub.get("anchor_band") or exp_anchor
                b_low = sub.get("band_low") or exp_low
                b_high = sub.get("band_high") or exp_high
                diag = sub.get("diagnostic_json") or sub.get("diagnostic") or {}

                create_subjective_assessment(
                    conn,
                    attempt_id=new_attempt_id,
                    learner_id=learner_id,
                    section=sub["section"],
                    estimated_score=est,
                    score_low=float(sub.get("score_low", est)),
                    score_high=float(sub.get("score_high", est)),
                    anchor_band=anchor,
                    band_low=b_low,
                    band_high=b_high,
                    confidence=sub.get("confidence", "medium"),
                    rubric_version=sub.get("rubric_version", "2026_OFFICIAL"),
                    assessor_model=sub.get("assessor_model", "assessor"),
                    skill_version=sub.get("skill_version", "1.0.0"),
                    diagnostic_json=diag,
                    assessor_provider=sub.get("assessor_provider"),
                    rationale_md=sub.get("rationale_md") or sub.get("overall_rationale"),
                    revision_md=sub.get("revision_md") or sub.get("revision"),
                )

        if error_events:
            for err in error_events:
                create_error_event(
                    conn,
                    learner_id=learner_id,
                    attempt_id=new_attempt_id,
                    section=err["section"],
                    subtype=err.get("subtype"),
                    taxonomy_code=err["taxonomy_code"],
                    severity=err.get("severity", 1),
                    evidence_note=err.get("evidence_note"),
                    resolved_state=err.get("resolved_state", "new"),
                )

    attempt = get_exam_attempt(conn, new_attempt_id, include_details=True)
    assert attempt is not None
    return attempt


# =====================================================================
# Section Result
# =====================================================================


def add_section_result(
    conn: sqlite3.Connection,
    *,
    section_result_id: str | None = None,
    attempt_id: str,
    section: str,
    subtype: str | None = None,
    correct_count: int | None = None,
    total_count: int | None = None,
    accuracy: float | None = None,
    duration_seconds: int | None = None,
    practice_index: float | None = None,
    provenance: str | None = None,
) -> dict[str, Any]:
    """Add a section result to an exam attempt with deterministic accuracy calculation."""
    if section not in VALID_SECTIONS:
        raise ValidationError(f"Invalid section '{section}', must be one of {VALID_SECTIONS}")

    if correct_count is not None and correct_count < 0:
        raise ValidationError("correct_count cannot be negative")

    if total_count is not None and total_count <= 0:
        raise ValidationError("total_count must be greater than 0")

    if correct_count is not None and total_count is not None:
        if correct_count > total_count:
            raise ValidationError(f"correct_count ({correct_count}) cannot exceed total_count ({total_count})")
        computed_acc = round(correct_count / total_count, 4)
        if accuracy is None:
            accuracy = computed_acc

    if accuracy is not None and not (0.0 <= accuracy <= 1.0):
        raise ValidationError(f"accuracy must be between 0.0 and 1.0, got {accuracy}")

    sec_id = section_result_id or str(uuid.uuid4())

    with conn:
        conn.execute(
            """
            INSERT INTO section_result (
                section_result_id, attempt_id, section, subtype,
                correct_count, total_count, accuracy, duration_seconds,
                practice_index, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                sec_id,
                attempt_id,
                section,
                subtype,
                correct_count,
                total_count,
                accuracy,
                duration_seconds,
                practice_index,
                provenance,
            ),
        )

    cursor = conn.execute(
        "SELECT * FROM section_result WHERE section_result_id = ?;",
        (sec_id,),
    )
    res = _row_to_dict(cursor.fetchone())
    assert res is not None
    return res


def list_section_results(
    conn: sqlite3.Connection,
    learner_id: str,
    *,
    section: str | None = None,
    subtype: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """List section results for a learner, chronologically sorted by attempted_at."""
    query = """
        SELECT sr.*, ea.attempted_at, ea.exam_level, ea.attempt_type
        FROM section_result sr
        JOIN exam_attempt ea ON sr.attempt_id = ea.attempt_id
        WHERE ea.learner_id = ?
    """
    params: list[Any] = [learner_id]

    if section:
        query += " AND sr.section = ?"
        params.append(section)
    if subtype:
        query += " AND sr.subtype = ?"
        params.append(subtype)

    query += " ORDER BY ea.attempted_at ASC"

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    cursor = conn.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


# =====================================================================
# Subjective Assessment
# =====================================================================


def create_subjective_assessment(
    conn: sqlite3.Connection,
    *,
    assessment_id: str | None = None,
    attempt_id: str | None = None,
    learner_id: str,
    section: str,
    estimated_score: int,
    score_low: int,
    score_high: int,
    anchor_band: int,
    band_low: int,
    band_high: int,
    confidence: str,
    rubric_version: str,
    assessor_model: str,
    skill_version: str,
    diagnostic_json: str | dict[str, Any],
    assessor_provider: str | None = None,
    rationale_md: str | None = None,
    revision_md: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Create a structured subjective assessment record for writing or translation."""
    if section not in VALID_SUBJECTIVE_SECTIONS:
        raise ValidationError(f"Invalid section '{section}' for subjective assessment")

    if not (1 <= estimated_score <= 15):
        raise ValidationError(f"estimated_score must be 1–15, got {estimated_score}")

    if not (score_low <= estimated_score <= score_high):
        raise ValidationError(
            f"estimated_score ({estimated_score}) must be between score_low ({score_low}) and score_high ({score_high})"
        )

    if anchor_band not in VALID_ANCHOR_BANDS:
        raise ValidationError(f"anchor_band must be one of {VALID_ANCHOR_BANDS}, got {anchor_band}")

    if band_low not in VALID_BAND_LOWS:
        raise ValidationError(f"band_low must be one of {VALID_BAND_LOWS}, got {band_low}")

    if band_high not in VALID_BAND_HIGHS:
        raise ValidationError(f"band_high must be one of {VALID_BAND_HIGHS}, got {band_high}")

    if not (band_low <= estimated_score <= band_high):
        raise ValidationError(
            f"estimated_score ({estimated_score}) must be within band [{band_low}, {band_high}]"
        )

    if confidence not in VALID_CONFIDENCES:
        raise ValidationError(f"confidence must be one of {VALID_CONFIDENCES}, got {confidence}")

    diagnostic_str = ensure_valid_json(diagnostic_json, "diagnostic_json")

    sub_id = assessment_id or str(uuid.uuid4())
    now = created_at or datetime.now(timezone.utc).isoformat()

    with conn:
        conn.execute(
            """
            INSERT INTO subjective_assessment (
                assessment_id, attempt_id, learner_id, section,
                estimated_score, score_low, score_high, anchor_band,
                band_low, band_high, confidence, rubric_version,
                assessor_provider, assessor_model, skill_version,
                diagnostic_json, rationale_md, revision_md, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                sub_id,
                attempt_id,
                learner_id,
                section,
                estimated_score,
                score_low,
                score_high,
                anchor_band,
                band_low,
                band_high,
                confidence,
                rubric_version,
                assessor_provider,
                assessor_model,
                skill_version,
                diagnostic_str,
                rationale_md,
                revision_md,
                now,
            ),
        )

    cursor = conn.execute(
        "SELECT * FROM subjective_assessment WHERE assessment_id = ?;",
        (sub_id,),
    )
    res = _row_to_dict(cursor.fetchone())
    assert res is not None
    return res


def get_subjective_assessment(
    conn: sqlite3.Connection,
    assessment_id: str,
) -> dict[str, Any] | None:
    """Get subjective assessment by ID."""
    cursor = conn.execute(
        "SELECT * FROM subjective_assessment WHERE assessment_id = ?;",
        (assessment_id,),
    )
    return _row_to_dict(cursor.fetchone())


def list_subjective_assessments(
    conn: sqlite3.Connection,
    learner_id: str,
    *,
    section: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List subjective assessments ordered chronologically."""
    query = "SELECT * FROM subjective_assessment WHERE learner_id = ?"
    params: list[Any] = [learner_id]

    if section:
        query += " AND section = ?"
        params.append(section)

    query += " ORDER BY created_at ASC LIMIT ?;"
    params.append(limit)

    cursor = conn.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


# =====================================================================
# Error Event
# =====================================================================


def create_error_event(
    conn: sqlite3.Connection,
    *,
    error_id: str | None = None,
    learner_id: str,
    attempt_id: str | None = None,
    section: str,
    subtype: str | None = None,
    taxonomy_code: str,
    severity: int = 1,
    evidence_note: str | None = None,
    resolved_state: str = "new",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Record an error event."""
    if section not in VALID_SECTIONS:
        raise ValidationError(f"Invalid section '{section}', must be one of {VALID_SECTIONS}")

    if severity not in (1, 2, 3):
        raise ValidationError(f"severity must be 1, 2, or 3, got {severity}")

    if resolved_state not in VALID_ERROR_RESOLVED_STATES:
        raise ValidationError(f"resolved_state must be one of {VALID_ERROR_RESOLVED_STATES}")

    if not taxonomy_code or not taxonomy_code.strip():
        raise ValidationError("taxonomy_code cannot be empty")

    err_id = error_id or str(uuid.uuid4())
    now = created_at or datetime.now(timezone.utc).isoformat()

    with conn:
        conn.execute(
            """
            INSERT INTO error_event (
                error_id, learner_id, attempt_id, section, subtype,
                taxonomy_code, severity, evidence_note, resolved_state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                err_id,
                learner_id,
                attempt_id,
                section,
                subtype,
                taxonomy_code.strip(),
                severity,
                evidence_note,
                resolved_state,
                now,
            ),
        )

    cursor = conn.execute(
        "SELECT * FROM error_event WHERE error_id = ?;",
        (err_id,),
    )
    res = _row_to_dict(cursor.fetchone())
    assert res is not None
    return res


def update_error_event_state(
    conn: sqlite3.Connection,
    error_id: str,
    resolved_state: str,
) -> dict[str, Any]:
    """Update the resolved state of an error event."""
    if resolved_state not in VALID_ERROR_RESOLVED_STATES:
        raise ValidationError(f"resolved_state must be one of {VALID_ERROR_RESOLVED_STATES}")

    with conn:
        cursor = conn.execute(
            "UPDATE error_event SET resolved_state = ? WHERE error_id = ?;",
            (resolved_state, error_id),
        )
        if cursor.rowcount == 0:
            raise RecordNotFoundError(f"Error event '{error_id}' not found")

    cursor = conn.execute("SELECT * FROM error_event WHERE error_id = ?;", (error_id,))
    res = _row_to_dict(cursor.fetchone())
    assert res is not None
    return res


def list_error_events(
    conn: sqlite3.Connection,
    learner_id: str,
    *,
    section: str | None = None,
    taxonomy_code: str | None = None,
    resolved_state: str | None = None,
) -> list[dict[str, Any]]:
    """List error events for a learner with optional filters."""
    query = "SELECT * FROM error_event WHERE learner_id = ?"
    params: list[Any] = [learner_id]

    if section:
        query += " AND section = ?"
        params.append(section)
    if taxonomy_code:
        query += " AND taxonomy_code = ?"
        params.append(taxonomy_code)
    if resolved_state:
        query += " AND resolved_state = ?"
        params.append(resolved_state)

    query += " ORDER BY created_at DESC;"

    cursor = conn.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


# =====================================================================
# Plan Version & Plan Item
# =====================================================================


def create_plan_version(
    conn: sqlite3.Connection,
    *,
    plan_id: str | None = None,
    learner_id: str,
    version: int | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    primary_priorities: str | list[Any] | dict[str, Any],
    rationale_md: str | None = None,
    created_by: str | None = None,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create an immutable plan version snapshot and optional plan items."""
    priorities_str = ensure_valid_json(primary_priorities, "primary_priorities")

    new_plan_id = plan_id or str(uuid.uuid4())
    start_time = valid_from or datetime.now(timezone.utc).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    with conn:
        if version is None:
            cursor = conn.execute(
                "SELECT coalesce(max(version), 0) + 1 FROM plan_version WHERE learner_id = ?;",
                (learner_id,),
            )
            version = cursor.fetchone()[0]

        # Close any previous unclosed plan version for this learner
        conn.execute(
            """
            UPDATE plan_version
            SET valid_to = ?
            WHERE learner_id = ? AND valid_to IS NULL AND plan_id != ?;
            """,
            (start_time, learner_id, new_plan_id),
        )

        conn.execute(
            """
            INSERT INTO plan_version (
                plan_id, learner_id, version, valid_from, valid_to,
                primary_priorities_json, rationale_md, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                new_plan_id,
                learner_id,
                version,
                start_time,
                valid_to,
                priorities_str,
                rationale_md,
                created_by,
                now,
            ),
        )

        if items:
            for item in items:
                status = item.get("status", "planned")
                if status not in VALID_PLAN_ITEM_STATUSES:
                    raise ValidationError(f"Invalid plan item status '{status}'")
                conn.execute(
                    """
                    INSERT INTO plan_item (
                        plan_item_id, plan_id, module, activity_type,
                        target_minutes, target_count, due_date, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        item.get("plan_item_id") or str(uuid.uuid4()),
                        new_plan_id,
                        item["module"],
                        item["activity_type"],
                        item.get("target_minutes"),
                        item.get("target_count"),
                        item.get("due_date"),
                        status,
                    ),
                )

    plan = get_plan_version(conn, new_plan_id)
    assert plan is not None
    return plan


def get_plan_version(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any] | None:
    """Get a plan version and its items."""
    cursor = conn.execute(
        "SELECT * FROM plan_version WHERE plan_id = ?;",
        (plan_id,),
    )
    plan = _row_to_dict(cursor.fetchone())
    if plan is None:
        return None

    items_cursor = conn.execute(
        "SELECT * FROM plan_item WHERE plan_id = ? ORDER BY due_date ASC;",
        (plan_id,),
    )
    plan["items"] = [dict(r) for r in items_cursor.fetchall()]
    return plan


def get_active_plan(conn: sqlite3.Connection, learner_id: str) -> dict[str, Any] | None:
    """Get the latest plan version for a learner."""
    cursor = conn.execute(
        "SELECT * FROM plan_version WHERE learner_id = ? ORDER BY version DESC LIMIT 1;",
        (learner_id,),
    )
    plan = _row_to_dict(cursor.fetchone())
    if plan is None:
        return None

    items_cursor = conn.execute(
        "SELECT * FROM plan_item WHERE plan_id = ? ORDER BY due_date ASC;",
        (plan["plan_id"],),
    )
    plan["items"] = [dict(r) for r in items_cursor.fetchall()]
    return plan


def list_plan_versions(conn: sqlite3.Connection, learner_id: str) -> list[dict[str, Any]]:
    """List all plan versions for a learner."""
    cursor = conn.execute(
        "SELECT * FROM plan_version WHERE learner_id = ? ORDER BY version DESC;",
        (learner_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def update_plan_item_status(
    conn: sqlite3.Connection,
    plan_item_id: str,
    status: str,
) -> dict[str, Any]:
    """Update the status of a plan item (planned, in_progress, done, skipped)."""
    if status not in VALID_PLAN_ITEM_STATUSES:
        raise ValidationError(f"Invalid plan item status '{status}', must be one of {VALID_PLAN_ITEM_STATUSES}")

    with conn:
        cursor = conn.execute(
            "UPDATE plan_item SET status = ? WHERE plan_item_id = ?;",
            (status, plan_item_id),
        )
        if cursor.rowcount == 0:
            raise RecordNotFoundError(f"Plan item '{plan_item_id}' not found")

    cursor = conn.execute("SELECT * FROM plan_item WHERE plan_item_id = ?;", (plan_item_id,))
    res = _row_to_dict(cursor.fetchone())
    assert res is not None
    return res


def get_plan_adherence(
    conn: sqlite3.Connection,
    learner_id: str,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Calculate adherence statistics for an active or specific plan version."""
    if plan_id:
        plan = get_plan_version(conn, plan_id)
    else:
        plan = get_active_plan(conn, learner_id)

    if not plan:
        return {
            "plan_id": None,
            "version": None,
            "total_items": 0,
            "done": 0,
            "in_progress": 0,
            "planned": 0,
            "skipped": 0,
            "adherence_rate": 0.0,
        }

    items = plan.get("items", [])
    total = len(items)
    done_count = sum(1 for it in items if it.get("status") == "done")
    in_progress_count = sum(1 for it in items if it.get("status") == "in_progress")
    planned_count = sum(1 for it in items if it.get("status") == "planned")
    skipped_count = sum(1 for it in items if it.get("status") == "skipped")
    rate = round(done_count / total, 4) if total > 0 else 0.0

    return {
        "plan_id": plan["plan_id"],
        "version": plan["version"],
        "total_items": total,
        "done": done_count,
        "in_progress": in_progress_count,
        "planned": planned_count,
        "skipped": skipped_count,
        "adherence_rate": rate,
    }


# =====================================================================
# Training Session
# =====================================================================


def create_training_session(
    conn: sqlite3.Connection,
    *,
    session_id: str | None = None,
    learner_id: str,
    planned_item_id: str | None = None,
    started_at: str | None = None,
    duration_seconds: int | None = None,
    module: str | None = None,
    activity_type: str | None = None,
    completed: bool = True,
    notes: str | None = None,
) -> dict[str, Any]:
    """Record an actual training session."""
    if module is not None and module not in VALID_TRAINING_MODULES:
        raise ValidationError(
            f"Invalid module '{module}', must be one of {VALID_TRAINING_MODULES}"
        )

    if duration_seconds is not None and duration_seconds < 0:
        raise ValidationError("duration_seconds cannot be negative")

    if get_learner_profile(conn, learner_id) is None:
        raise RecordNotFoundError(f"Learner '{learner_id}' not found")

    if planned_item_id is not None:
        item = conn.execute(
            """
            SELECT pi.plan_item_id
            FROM plan_item AS pi
            JOIN plan_version AS pv ON pv.plan_id = pi.plan_id
            WHERE pi.plan_item_id = ? AND pv.learner_id = ?;
            """,
            (planned_item_id, learner_id),
        ).fetchone()
        if item is None:
            raise RecordNotFoundError(
                f"Plan item '{planned_item_id}' was not found for learner '{learner_id}'"
            )

    sess_id = session_id or str(uuid.uuid4())
    start_time = started_at or datetime.now(timezone.utc).isoformat()

    with conn:
        conn.execute(
            """
            INSERT INTO training_session (
                session_id, learner_id, planned_item_id, started_at,
                duration_seconds, module, activity_type, completed, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                sess_id,
                learner_id,
                planned_item_id,
                start_time,
                duration_seconds,
                module,
                activity_type,
                1 if completed else 0,
                notes,
            ),
        )

    cursor = conn.execute("SELECT * FROM training_session WHERE session_id = ?;", (sess_id,))
    res = _row_to_dict(cursor.fetchone())
    assert res is not None
    return res


def get_training_session(
    conn: sqlite3.Connection,
    session_id: str,
) -> dict[str, Any] | None:
    """Get one training session by ID."""
    cursor = conn.execute(
        "SELECT * FROM training_session WHERE session_id = ?;",
        (session_id,),
    )
    return _row_to_dict(cursor.fetchone())


def list_training_sessions(
    conn: sqlite3.Connection,
    learner_id: str,
    *,
    module: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List training sessions for a learner, newest first."""
    if module is not None and module not in VALID_TRAINING_MODULES:
        raise ValidationError(f"Invalid module '{module}', must be one of {VALID_TRAINING_MODULES}")
    if limit <= 0:
        raise ValidationError("limit must be greater than 0")

    query = "SELECT * FROM training_session WHERE learner_id = ?"
    params: list[Any] = [learner_id]
    if module is not None:
        query += " AND module = ?"
        params.append(module)
    query += " ORDER BY started_at DESC LIMIT ?;"
    params.append(limit)
    cursor = conn.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]
