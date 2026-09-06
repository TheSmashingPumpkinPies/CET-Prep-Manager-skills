# Runtime Setup and Database Path Contract

Read this reference before the first persistence call when the Python environment or learner
database location is unknown.

## Preflight

1. Run `python -m cet_prep_manager version --short` (or `cetpm version --short`).
2. If the module or command is unavailable, stop before claiming that anything was recorded.
   Explain that the package must be installed from the CET Prep Manager repository with
   `python -m pip install -e .` for development or `python -m pip install .` for a local runtime.
   Do not install software without the user's authorization when that action requires it.
3. Resolve one absolute SQLite path for the learner. Prefer, in order:
   - an explicit `--db-path` supplied by the user;
   - the existing `CETPM_DB_PATH` environment variable;
   - `<project-root>/data/cet_prep.db` when the project root is known.
4. Reuse that exact path for every command in the session. Set `CETPM_DB_PATH` in the command
   environment or append `--db-path "<absolute-path>"` to every invocation.

## Safety and continuity

- Do not search for and merge multiple database files automatically.
- If more than one plausible learner database exists, ask the user which one is authoritative.
- Do not use the current-working-directory default from an arbitrary directory: without
  `CETPM_DB_PATH` or `--db-path`, the CLI resolves `data/cet_prep.db` under the current directory.
- `cetpm init` and normal commands may create a missing database, so confirm the resolved path
  before the first write.
- A successful write must be confirmed by the command's exit status and returned record ID.

## Repository and Skill installation

The `skill/` directory contains agent instructions; the Python package under `src/` provides the
actual CLI. Installing one does not install the other. For Codex, install the Skill contents in a
folder named `cet-prep-manager` under the user's skills directory, and install the Python package
from the repository root. See the repository `README.md` for the complete quick start.
