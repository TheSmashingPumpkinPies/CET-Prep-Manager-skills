"""Pydantic domain models for CET Prep Manager (DB-003).

Mirrors the JSON schemas and database constraints.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# =====================================================================
# Enums
# =====================================================================


class ExamLevel(str, Enum):
    CET4 = "CET4"
    CET6 = "CET6"


class AttemptType(str, Enum):
    FULL_MOCK = "full_mock"
    SECTION_PRACTICE = "section_practice"
    OFFICIAL_EXAM = "official_exam"
    DIAGNOSTIC = "diagnostic"


class SourceKind(str, Enum):
    PAST_PAPER = "past_paper"
    COMMERCIAL_MOCK = "commercial_mock"
    GENERATED = "generated"
    OFFICIAL_RESULT = "official_result"
    OTHER = "other"


class SectionName(str, Enum):
    LISTENING = "listening"
    READING = "reading"
    WRITING = "writing"
    TRANSLATION = "translation"
    OVERALL = "overall"


class ObjectiveSubtype(str, Enum):
    SHORT_NEWS = "short_news"
    LONG_CONVERSATIONS = "long_conversations"
    LISTENING_PASSAGES = "listening_passages"
    TALKS_REPORTS_LECTURES = "talks_reports_lectures"
    BANKED_CLOZE = "banked_cloze"
    LONG_READING_MATCHING = "long_reading_matching"
    CAREFUL_READING = "careful_reading"


class SubjectiveSectionName(str, Enum):
    WRITING = "writing"
    TRANSLATION = "translation"


class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ErrorResolvedState(str, Enum):
    NEW = "new"
    RECURRENT = "recurrent"
    IMPROVING = "improving"
    RESOLVED = "resolved"


class PlanItemStatus(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


class TrainingModule(str, Enum):
    LISTENING = "listening"
    READING = "reading"
    WRITING = "writing"
    TRANSLATION = "translation"
    VOCABULARY = "vocabulary"
    FULL_MOCK = "full_mock"


class DataSufficiency(str, Enum):
    INSUFFICIENT = "insufficient"
    EMERGING = "emerging"
    USABLE = "usable"


# Standard CET anchor bands and their low/high ranges
BAND_SPEC = {
    14: (13, 15),
    11: (10, 12),
    8: (7, 9),
    5: (4, 6),
    2: (1, 3),
}


def get_band_info_for_score(score: int) -> tuple[int, int, int]:
    """Return (anchor_band, band_low, band_high) for an estimated score (1–15)."""
    if not (1 <= score <= 15):
        raise ValueError(f"Score must be 1–15, got {score}")
    for anchor, (low, high) in BAND_SPEC.items():
        if low <= score <= high:
            return anchor, low, high
    raise ValueError(f"Score {score} does not match any valid band")


# =====================================================================
# Domain Models
# =====================================================================


class LearnerProfileModel(BaseModel):
    """Learner Profile domain model."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    learner_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    display_name: str | None = None
    target_exam: ExamLevel
    target_exam_date: str | None = None
    target_reported_score: int | None = None
    daily_minutes: int | None = Field(default=None, ge=0)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SectionResultModel(BaseModel):
    """Section Result within an exam attempt."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    section_result_id: str | None = None
    attempt_id: str | None = None
    section: SectionName
    subtype: ObjectiveSubtype | None = None
    correct_count: int | None = Field(default=None, ge=0)
    total_count: int | None = Field(default=None, ge=1)
    accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    duration_seconds: int | None = Field(default=None, ge=0)
    practice_index: float | None = None
    provenance: str | None = None

    @model_validator(mode="after")
    def compute_and_verify_accuracy(self) -> SectionResultModel:
        if self.correct_count is not None and self.total_count is not None:
            if self.correct_count > self.total_count:
                raise ValueError(
                    f"correct_count ({self.correct_count}) cannot exceed total_count ({self.total_count})"
                )
            computed = round(self.correct_count / self.total_count, 4)
            if self.accuracy is None:
                self.accuracy = computed
        return self


class SubjectiveAssessmentModel(BaseModel):
    """Subjective Assessment for writing and translation.

    Aligns with schemas/subjective_assessment.schema.json and DB schema.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    assessment_id: str | None = None
    attempt_id: str | None = None
    learner_id: str | None = None
    section: SubjectiveSectionName
    estimated_score: int = Field(ge=1, le=15)
    score_low: int = Field(ge=1, le=15)
    score_high: int = Field(ge=1, le=15)
    anchor_band: int = Field(default=0)
    band_low: int = Field(default=0)
    band_high: int = Field(default=0)
    confidence: ConfidenceLevel
    rubric_version: str = Field(min_length=1)
    assessor_provider: str | None = None
    assessor_model: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    diagnostic: dict[str, float] = Field(default_factory=dict)
    error_codes: list[str] = Field(default_factory=list)
    overall_rationale: str | None = None
    revision: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @model_validator(mode="after")
    def validate_score_and_band_consistency(self) -> SubjectiveAssessmentModel:
        # Verify score range
        if not (self.score_low <= self.estimated_score <= self.score_high):
            raise ValueError(
                f"estimated_score ({self.estimated_score}) must be between score_low ({self.score_low}) and score_high ({self.score_high})"
            )

        # Inferred band if omitted or 0
        expected_anchor, expected_low, expected_high = get_band_info_for_score(self.estimated_score)
        if self.anchor_band == 0:
            self.anchor_band = expected_anchor
        if self.band_low == 0:
            self.band_low = expected_low
        if self.band_high == 0:
            self.band_high = expected_high

        # Validate band constraints
        if self.anchor_band not in BAND_SPEC:
            raise ValueError(f"Invalid anchor_band {self.anchor_band}")
        if (self.band_low, self.band_high) != BAND_SPEC[self.anchor_band]:
            raise ValueError(
                f"anchor_band {self.anchor_band} must have band [{BAND_SPEC[self.anchor_band][0]}, {BAND_SPEC[self.anchor_band][1]}]"
            )
        if not (self.band_low <= self.estimated_score <= self.band_high):
            raise ValueError(
                f"estimated_score ({self.estimated_score}) must be within band [{self.band_low}, {self.band_high}]"
            )

        # Validate diagnostic dimensions (0..100)
        for dim, score in self.diagnostic.items():
            if not (0.0 <= score <= 100.0):
                raise ValueError(f"Diagnostic score for '{dim}' must be between 0 and 100, got {score}")

        return self

    def to_repo_dict(self) -> dict[str, Any]:
        """Convert to the dictionary format expected by the DB repository."""
        return {
            "assessment_id": self.assessment_id,
            "attempt_id": self.attempt_id,
            "learner_id": self.learner_id,
            "section": self.section.value,
            "estimated_score": self.estimated_score,
            "score_low": self.score_low,
            "score_high": self.score_high,
            "anchor_band": self.anchor_band,
            "band_low": self.band_low,
            "band_high": self.band_high,
            "confidence": self.confidence.value,
            "rubric_version": self.rubric_version,
            "assessor_provider": self.assessor_provider,
            "assessor_model": self.assessor_model,
            "skill_version": self.skill_version,
            "diagnostic_json": self.diagnostic,
            "rationale_md": self.overall_rationale,
            "revision_md": self.revision,
            "created_at": self.created_at,
        }


class ErrorEventModel(BaseModel):
    """Error Event domain model."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    error_id: str | None = None
    learner_id: str | None = None
    attempt_id: str | None = None
    section: SectionName
    subtype: str | None = None
    taxonomy_code: str = Field(min_length=1)
    severity: int = Field(default=1, ge=1, le=3)
    evidence_note: str | None = None
    resolved_state: ErrorResolvedState = ErrorResolvedState.NEW
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ExamAttemptModel(BaseModel):
    """Exam Attempt domain model.

    Aligns with schemas/exam_attempt.schema.json.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    attempt_id: str | None = None
    learner_id: str | None = None
    attempted_at: str
    exam_level: ExamLevel
    attempt_type: AttemptType
    source_key: str | None = None
    source_kind: SourceKind | None = None
    official_reported_score: int | None = Field(default=None, ge=0, le=710)
    official_listening_score: int | None = Field(default=None, ge=0, le=249)
    official_reading_score: int | None = Field(default=None, ge=0, le=249)
    official_writing_translation_score: int | None = Field(default=None, ge=0, le=212)
    duration_seconds: int | None = Field(default=None, ge=0)
    notes: str | None = None
    idempotency_key: str | None = None
    sections: list[SectionResultModel] = Field(default_factory=list)
    subjective_assessments: list[SubjectiveAssessmentModel] = Field(default_factory=list)
    error_events: list[ErrorEventModel] = Field(default_factory=list)
    created_at: str | None = None

    @model_validator(mode="after")
    def validate_official_reported_score(self) -> ExamAttemptModel:
        official_scores = (
            self.official_reported_score,
            self.official_listening_score,
            self.official_reading_score,
            self.official_writing_translation_score,
        )
        if any(score is not None for score in official_scores) and self.attempt_type != AttemptType.OFFICIAL_EXAM:
            raise ValueError(
                "official reported scores may only be stored for attempt_type='official_exam'"
            )
        components = official_scores[1:]
        if self.official_reported_score is not None and all(score is not None for score in components):
            component_total = sum(score for score in components if score is not None)
            if component_total != self.official_reported_score:
                raise ValueError(
                    "official component scores must sum to official_reported_score"
                )
        return self


class PlanItemModel(BaseModel):
    """Plan Item domain model."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    plan_item_id: str | None = None
    plan_id: str | None = None
    module: str
    activity_type: str
    target_minutes: int | None = Field(default=None, ge=0)
    target_count: int | None = Field(default=None, ge=0)
    due_date: str | None = None
    status: PlanItemStatus = PlanItemStatus.PLANNED


class PlanVersionModel(BaseModel):
    """Plan Version domain model."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    plan_id: str | None = None
    learner_id: str
    version: int = 1
    valid_from: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    valid_to: str | None = None
    primary_priorities: list[str] | dict[str, Any] | str
    rationale_md: str | None = None
    created_by: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    items: list[PlanItemModel] = Field(default_factory=list)


class TrainingSessionModel(BaseModel):
    """One completed or attempted learner training activity."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str | None = None
    learner_id: str | None = None
    planned_item_id: str | None = None
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_seconds: int | None = Field(default=None, ge=0)
    module: TrainingModule | None = None
    activity_type: str | None = None
    completed: bool = True
    notes: str | None = None


class ModuleState(BaseModel):
    """Analytical summary state of a single preparation module."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    latest: float | None = None
    ma3: float | None = None
    ma5: float | None = None
    trend_slope: float | None = None
    volatility: float | None = None
    n: int = Field(default=0, ge=0)
    sufficiency: DataSufficiency = DataSufficiency.INSUFFICIENT


class ModuleBalance(BaseModel):
    """Cross-module diagnostic balance using normalized latest observations."""

    method: str = "objective_accuracy_and_subjective_score_divided_by_15"
    normalized_latest: dict[str, float] = Field(default_factory=dict)
    available_modules: int = Field(default=0, ge=0)
    mean: float | None = None
    gap: float | None = None
    strongest_module: str | None = None
    weakest_module: str | None = None
    is_balanced: bool | None = None


class LearnerStateSnapshot(BaseModel):
    """Full learner state snapshot for cross-agent recovery.

    Aligns with schemas/learner_state.schema.json.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    learner_id: str
    target_exam: ExamLevel
    target_exam_date: str | None = None
    target_reported_score: int | None = None
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    modules: dict[str, ModuleState] = Field(default_factory=dict)
    module_balance: ModuleBalance = Field(default_factory=ModuleBalance)
    top_error_codes: list[str] = Field(default_factory=list)
    active_plan_id: str | None = None
