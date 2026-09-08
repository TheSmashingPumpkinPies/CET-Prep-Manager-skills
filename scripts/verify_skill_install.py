"""Verify a CET Prep Manager Skill tree against its approved hash manifest."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

MANIFEST_NAME = "SHA256SUMS"


def sha256(path: Path) -> str:
    # Git may check text files out as CRLF on Windows. Hash their canonical LF
    # representation so the same approved Skill verifies across platforms.
    canonical_bytes = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(canonical_bytes).hexdigest()


def load_manifest(source: Path) -> dict[str, str]:
    manifest = source / MANIFEST_NAME
    if not manifest.is_file():
        raise ValueError(f"Missing manifest: {manifest}")
    expected: dict[str, str] = {}
    for line_number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise ValueError(f"Invalid manifest line {line_number}") from exc
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"Invalid SHA-256 on manifest line {line_number}")
        expected[relative] = digest
    return expected


def tree_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != MANIFEST_NAME and "__pycache__" not in path.parts
    }


def verify(source: Path, installed: Path) -> list[str]:
    expected = load_manifest(source)
    failures: list[str] = []

    source_files = tree_files(source)
    if source_files != set(expected):
        expected_names = sorted(expected)
        actual_names = sorted(source_files)
        failures.append(
            f"source manifest coverage differs: expected={expected_names}, actual={actual_names}"
        )

    for relative, digest in expected.items():
        source_file = source / relative
        installed_file = installed / relative
        if not source_file.is_file() or sha256(source_file) != digest:
            failures.append(f"source hash mismatch: {relative}")
        if not installed_file.is_file():
            failures.append(f"installed file missing: {relative}")
        elif sha256(installed_file) != digest:
            failures.append(f"installed hash mismatch: {relative}")

    installed_files = tree_files(installed)
    extras = installed_files - set(expected)
    for relative in sorted(extras):
        failures.append(f"unexpected installed file: {relative}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("installed", type=Path)
    args = parser.parse_args()

    source = args.source.resolve()
    installed = args.installed.resolve()
    failures = verify(source, installed)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    version = (source / "VERSION").read_text(encoding="utf-8").strip()
    print(f"OK: {installed} matches CET Prep Manager Skill {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
