#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${VIRTUAL_ENV:-$ROOT_DIR/.venv}"
PYTHON="$VENV_DIR/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Slicer Python environment is missing; rerun .devcontainer/post-create.sh." >&2
  exit 1
fi

if [[ -z "${SLICER_MCP_BEARER_TOKEN:-}" ]]; then
  echo "SLICER_MCP_BEARER_TOKEN is not configured; cloud slicer origin will not start." >&2
  exit 0
fi

if [[ ! -x ".tools/bin/bambu-studio" ]]; then
  bash scripts/slicer/install-bambu-studio.sh
fi

pid_file="/tmp/windsor-slicer-mcp.pid"
log_file="/tmp/windsor-slicer-mcp.log"

if [[ -f "$pid_file" ]]; then
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    exit 0
  fi
fi

# The background service has a dedicated rotating activity log, so avoid
# duplicating every activity line into the general nohup log. Foreground/manual
# runs still default to live stderr activity output.
nohup env PATH="$VENV_DIR/bin:$PATH" SLICER_MCP_ACTIVITY_STDERR=0 \
  bash scripts/run-slicer-mcp.sh >"$log_file" 2>&1 &
echo "$!" > "$pid_file"

for _ in {1..30}; do
  if "$PYTHON" - <<'PY'
import socket
with socket.create_connection(("127.0.0.1", 8000), timeout=1):
    pass
PY
  then
    echo "Slicer MCP is listening on port 8000."
    exit 0
  fi
  sleep 1
done

echo "LED slicer MCP did not become ready; see $log_file" >&2
exit 1
