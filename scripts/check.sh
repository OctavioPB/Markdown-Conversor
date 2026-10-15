#!/usr/bin/env bash
# Local CI: lint, format, types and fast tests. Must pass before closing any task.
set -euo pipefail
cd "$(dirname "$0")/.."

uv run ruff check .
uv run ruff format --check .
uv run mypy src scripts
uv run pytest -m "not slow"
