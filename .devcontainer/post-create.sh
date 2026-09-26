#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  python3 python3-pip python3-venv \
  openscad curl ca-certificates xvfb xauth squashfs-tools \
  libgl1 libglu1-mesa libosmesa6 \
  libgstreamer-plugins-base1.0-0 libwebkit2gtk-4.1-0

venv_dir="${VIRTUAL_ENV:-$ROOT_DIR/.venv}"
python3 -m venv "$venv_dir"

"$venv_dir/bin/python" -m pip install --disable-pip-version-check \
  trimesh manifold3d numpy networkx scipy

"$venv_dir/bin/python" -m pip install --disable-pip-version-check \
  -r mcp_servers/slicer/requirements.txt

# Install the pinned Bambu Studio runtime once when the Codespace is created.
# .tools is retained in the Codespace filesystem and reused on ordinary restarts.
bash scripts/slicer/install-bambu-studio.sh

cat <<'EOF'

Windsor Slicer Codespace ready.

Install the pinned Bambu Studio CLI on demand:
  bash scripts/slicer/install-bambu-studio.sh

Consumer models are prepared from immutable repository commits through the MCP.

To experiment with Bambu auto-orientation:
  SLICER_ORIENT=1 bash scripts/slicer/slice-stl.sh <model.stl>

Run the slicer MCP over stdio:
  bash scripts/run-slicer-mcp.sh

Run the slicer MCP over Streamable HTTP on the forwarded port:
  SLICER_MCP_TRANSPORT=streamable-http SLICER_MCP_HOST=0.0.0.0 \
    bash scripts/run-slicer-mcp.sh

EOF
