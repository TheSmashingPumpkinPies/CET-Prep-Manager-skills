PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1');

CREATE TABLE IF NOT EXISTS learner_profile (
    learner_id TEXT PRIMARY KEY,
    display_name TEXT,
    target_exam TEXT NOT NULL CHECK(target_exam IN ('CET4','CET6')),
    target_exam_date TEXT,
    target_reported_score INTEGER,
    daily_minutes INTEGER CHECK(daily_minutes IS NULL OR daily_minutes >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exam_attempt (
    attempt_id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL REFERENCES learner_profile(learner_id),
    attempted_at TEXT NOT NULL,
    exam_level TEXT NOT NULL CHECK(exam_level IN ('CET4','CET6')),
    attempt_type TEXT NOT NULL CHECK(attempt_type IN ('full_mock','section_practice','official_exam','diagnostic')),
    source_key TEXT,
    source_kind TEXT CHECK(source_kind IS NULL OR source_kind IN ('past_paper','commercial_mock','generated','official_result','other')),
    duration_seconds INTEGER CHECK(duration_seconds IS NULL OR duration_seconds >= 0),
    notes TEXT,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS section_result (
    section_result_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES exam_attempt(attempt_id) ON DELETE CASCADE,
    section TEXT NOT NULL CHECK(section IN ('listening','reading','writing','translation','overall')),
    subtype TEXT,
    correct_count INTEGER CHECK(correct_count IS NULL OR correct_count >= 0),
    total_count INTEGER CHECK(total_count IS NULL OR total_count > 0),
    accuracy REAL CHECK(accuracy IS NULL OR (accuracy >= 0 AND accuracy <= 1)),
    duration_seconds INTEGER CHECK(duration_seconds IS NULL OR duration_seconds >= 0),
    practice_index REAL,
    provenance TEXT,
    CHECK(correct_count IS NULL OR total_count IS NULL OR correct_count <= total_count)
);

CREATE TABLE IF NOT EXISTS subjective_assessment (
    assessment_id TEXT PRIMARY KEY,
    attempt_id TEXT REFERENCES exam_attempt(attempt_id) ON DELETE SET NULL,
    learner_id TEXT NOT NULL REFERENCES learner_profile(learner_id),
    section TEXT NOT NULL CHECK(section IN ('writing','translation')),
    estimated_score INTEGER NOT NULL CHECK(estimated_score BETWEEN 1 AND 15),
    score_low INTEGER NOT NULL CHECK(score_low BETWEEN 1 AND 15),
    score_high INTEGER NOT NULL CHECK(score_high BETWEEN 1 AND 15),
    anchor_band INTEGER NOT NULL CHECK(anchor_band IN (14,11,8,5,2)),
    band_low INTEGER NOT NULL CHECK(band_low IN (1,4,7,10,13)),
    band_high INTEGER NOT NULL CHECK(band_high IN (3,6,9,12,15)),
    confidence TEXT NOT NULL CHECK(confidence IN ('low','medium','high')),
    rubric_version TEXT NOT NULL,
    assessor_provider TEXT,
    assessor_model TEXT NOT NULL,
    skill_version TEXT NOT NULL,
    diagnostic_json TEXT NOT NULL,
    rationale_md TEXT,
    revision_md TEXT,
    created_at TEXT NOT NULL,
    CHECK(score_low <= estimated_score AND estimated_score <= score_high),
    CHECK(band_low <= estimated_score AND estimated_score <= band_high)
);

CREATE TABLE IF NOT EXISTS error_event (
    error_id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL REFERENCES learner_profile(learner_id),
    attempt_id TEXT REFERENCES exam_attempt(attempt_id) ON DELETE SET NULL,
    section TEXT NOT NULL CHECK(section IN ('listening','reading','writing','translation')),
    subtype TEXT,
    taxonomy_code TEXT NOT NULL,
    severity INTEGER NOT NULL DEFAULT 1 CHECK(severity BETWEEN 1 AND 3),
    evidence_note TEXT,
    resolved_state TEXT NOT NULL DEFAULT 'new' CHECK(resolved_state IN ('new','recurrent','improving','resolved')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_session (
    session_id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL REFERENCES learner_profile(learner_id),
    planned_item_id TEXT,
    started_at TEXT NOT NULL,
    duration_seconds INTEGER CHECK(duration_seconds IS NULL OR duration_seconds >= 0),
    module TEXT CHECK(module IS NULL OR module IN ('listening','reading','writing','translation','vocabulary','full_mock')),
    activity_type TEXT,
    completed INTEGER NOT NULL DEFAULT 1 CHECK(completed IN (0,1)),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS plan_version (
    plan_id TEXT PRIMARY KEY,
    learner_id TEXT NOT NULL REFERENCES learner_profile(learner_id),
    version INTEGER NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    primary_priorities_json TEXT NOT NULL,
    rationale_md TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(learner_id, version)
);

CREATE TABLE IF NOT EXISTS plan_item (
    plan_item_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plan_version(plan_id) ON DELETE CASCADE,
    module TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    target_minutes INTEGER CHECK(target_minutes IS NULL OR target_minutes >= 0),
    target_count INTEGER CHECK(target_count IS NULL OR target_count >= 0),
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','in_progress','done','skipped'))
);

CREATE INDEX IF NOT EXISTS idx_attempt_learner_date ON exam_attempt(learner_id, attempted_at);
CREATE INDEX IF NOT EXISTS idx_section_attempt ON section_result(attempt_id, section);
CREATE INDEX IF NOT EXISTS idx_assessment_learner_section_date ON subjective_assessment(learner_id, section, created_at);
CREATE INDEX IF NOT EXISTS idx_error_learner_code_date ON error_event(learner_id, taxonomy_code, created_at);
