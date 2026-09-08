"""Canonical objective CET paper structure and aggregation rules."""

from __future__ import annotations

from typing import Any

# Internal identifiers remain stable; user-facing Chinese labels live in the Skill reference.
OBJECTIVE_COMPONENTS: dict[str, dict[str, dict[str, dict[str, float | int]]]] = {
    "CET4": {
        "listening": {
            "short_news": {"question_count": 7, "paper_weight": 0.07},
            "long_conversations": {"question_count": 8, "paper_weight": 0.08},
            "listening_passages": {"question_count": 10, "paper_weight": 0.20},
        },
        "reading": {
            "banked_cloze": {"question_count": 10, "paper_weight": 0.05},
            "long_reading_matching": {"question_count": 10, "paper_weight": 0.10},
            "careful_reading": {"question_count": 10, "paper_weight": 0.20},
        },
    },
    "CET6": {
        "listening": {
            "long_conversations": {"question_count": 8, "paper_weight": 0.08},
            "listening_passages": {"question_count": 7, "paper_weight": 0.07},
            "talks_reports_lectures": {"question_count": 10, "paper_weight": 0.20},
        },
        "reading": {
            "banked_cloze": {"question_count": 10, "paper_weight": 0.05},
            "long_reading_matching": {"question_count": 10, "paper_weight": 0.10},
            "careful_reading": {"question_count": 10, "paper_weight": 0.20},
        },
    },
}

CANONICAL_OBJECTIVE_SUBTYPES = frozenset(
    subtype
    for exam in OBJECTIVE_COMPONENTS.values()
    for section in exam.values()
    for subtype in section
)


def validate_objective_result(
    *,
    exam_level: str,
    attempt_type: str,
    section: str,
    subtype: str | None,
    total_count: int | None,
) -> None:
    """Validate canonical subtype names and standard full-paper question counts."""
    if section not in {"listening", "reading"}:
        if subtype is not None:
            raise ValueError(f"section '{section}' does not accept an objective subtype")
        return

    section_spec = OBJECTIVE_COMPONENTS[exam_level][section]
    if subtype in {None, "overall"}:
        if attempt_type == "full_mock" and total_count is not None:
            expected = sum(int(item["question_count"]) for item in section_spec.values())
            if total_count != expected:
                raise ValueError(
                    f"standard {exam_level} {section} overall result must contain "
                    f"{expected} questions, got {total_count}"
                )
        return

    if subtype not in section_spec:
        allowed = ", ".join(section_spec)
        raise ValueError(
            f"invalid {exam_level} {section} subtype '{subtype}'; use one of: {allowed}"
        )
    if attempt_type == "full_mock" and total_count is not None:
        expected = int(section_spec[subtype]["question_count"])
        if total_count != expected:
            raise ValueError(
                f"standard {exam_level} {subtype} result must contain "
                f"{expected} questions, got {total_count}"
            )


def _row_accuracy(row: dict[str, Any]) -> float | None:
    if row.get("accuracy") is not None:
        return float(row["accuracy"])
    correct = row.get("correct_count")
    total = row.get("total_count")
    if correct is not None and total:
        return float(correct) / float(total)
    return None


def aggregate_objective_attempt(rows: list[dict[str, Any]], section: str) -> float | None:
    """Return one module observation for one attempt.

    Complete canonical component data is combined using official paper weights. Otherwise an
    explicitly recorded overall row is used. Partial component data is deliberately not promoted
    to a whole-module observation.
    """
    if not rows:
        return None
    exam_level = str(rows[0]["exam_level"])
    section_spec = OBJECTIVE_COMPONENTS.get(exam_level, {}).get(section)
    if section_spec:
        by_subtype = {row.get("subtype"): row for row in rows}
        if all(subtype in by_subtype for subtype in section_spec):
            weighted_sum = 0.0
            section_weight = 0.0
            for subtype, spec in section_spec.items():
                accuracy = _row_accuracy(by_subtype[subtype])
                if accuracy is None:
                    break
                weight = float(spec["paper_weight"])
                weighted_sum += accuracy * weight
                section_weight += weight
            else:
                return round(weighted_sum / section_weight, 4)

    overall_rows = [row for row in rows if row.get("subtype") in {None, "overall"}]
    if overall_rows:
        accuracy = _row_accuracy(overall_rows[-1])
        return None if accuracy is None else round(accuracy, 4)
    return None
