"""Database exceptions for CET Prep Manager."""

from __future__ import annotations


class CetPrepManagerDbError(Exception):
    """Base exception for CET Prep Manager database operations."""


class DuplicateIdempotencyKeyError(CetPrepManagerDbError):
    """Raised when an attempt with the same idempotency key already exists."""

    def __init__(self, key: str, existing_attempt_id: str | None = None) -> None:
        self.key = key
        self.existing_attempt_id = existing_attempt_id
        msg = f"Exam attempt with idempotency key '{key}' already exists"
        if existing_attempt_id:
            msg += f" (attempt_id: {existing_attempt_id})"
        super().__init__(msg)


class ValidationError(CetPrepManagerDbError):
    """Raised when input data violates domain or schema validation rules."""


class RecordNotFoundError(CetPrepManagerDbError):
    """Raised when a requested database record is not found."""
