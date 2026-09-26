#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VIRTUAL_ENV:-$ROOT_DIR/.venv}"
PYTHON="$VENV_DIR/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Slicer Python environment is missing." >&2
  echo "Run: bash .devcontainer/post-create.sh" >&2
  exit 1
fi

exec "$PYTHON" scripts/slicer/codespace-mcp-smoke.py "$@"
