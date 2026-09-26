#!/usr/bin/env bash
set -euo pipefail

# Install a pinned Bambu Studio AppImage for headless CLI use.
# The default version is intentionally stable and can be overridden explicitly.
BAMBU_STUDIO_VERSION="${BAMBU_STUDIO_VERSION:-v02.08.02.61}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOOLS_DIR="${SLICER_TOOLS_DIR:-$ROOT_DIR/.tools}"
VERSION_DIR="$TOOLS_DIR/bambu-studio/$BAMBU_STUDIO_VERSION"
APPIMAGE="$VERSION_DIR/BambuStudio.AppImage"
EXTRACTED="$VERSION_DIR/app"
BIN_DIR="$TOOLS_DIR/bin"
WRAPPER="$BIN_DIR/bambu-studio"

mkdir -p "$VERSION_DIR" "$BIN_DIR"

if [[ -x "$EXTRACTED/AppRun" ]]; then
  echo "Bambu Studio $BAMBU_STUDIO_VERSION already installed at $EXTRACTED"
else
  command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
  command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }
  command -v unsquashfs >/dev/null || {
    echo "unsquashfs is required (install squashfs-tools)" >&2
    exit 2
  }

  api_url="https://api.github.com/repos/bambulab/BambuStudio/releases/tags/$BAMBU_STUDIO_VERSION"
  auth_args=()
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    auth_args=(-H "Authorization: Bearer $GITHUB_TOKEN")
  fi

  echo "Resolving Bambu Studio $BAMBU_STUDIO_VERSION Ubuntu 24.04 AppImage..."
  release_json="$(curl -fsSL "${auth_args[@]}" -H 'Accept: application/vnd.github+json' "$api_url")"
  asset_url="$(python3 -c '
import json, sys
data=json.load(sys.stdin)
assets=data.get("assets", [])
candidates=[]
for asset in assets:
    name=asset.get("name", "")
    lower=name.lower()
    if not lower.endswith(".appimage"):
        continue
    if "ubuntu" not in lower or "24.04" not in lower:
        continue
    if "aarch64" in lower or "arm64" in lower:
        continue
    candidates.append((name, asset.get("browser_download_url", "")))
if not candidates:
    raise SystemExit("No x86_64 Ubuntu 24.04 AppImage found in release")
candidates.sort()
print(candidates[-1][1])
' <<<"$release_json")"

  if [[ -z "$asset_url" ]]; then
    echo "Unable to resolve Bambu Studio release asset" >&2
    exit 3
  fi

  tmp="$APPIMAGE.tmp"
  rm -f "$tmp"
  echo "Downloading Bambu Studio..."
  curl -fL --retry 3 --retry-delay 2 -o "$tmp" "$asset_url"
  mv "$tmp" "$APPIMAGE"
  chmod +x "$APPIMAGE"

  rm -rf "$EXTRACTED"

  # Do not depend on FUSE or the AppImage's extract helper in CI. Asking the
  # AppImage runtime for its SquashFS byte offset and extracting it directly
  # is deterministic on GitHub-hosted runners and still exposes the bundled
  # resources/profiles tree needed by the CLI.
  offset="$("$APPIMAGE" --appimage-offset)"
  if [[ ! "$offset" =~ ^[0-9]+$ ]]; then
    echo "Unable to determine AppImage SquashFS offset: $offset" >&2
    exit 4
  fi
  echo "Extracting AppImage payload at byte offset $offset..."
  unsquashfs -q -o "$offset" -d "$EXTRACTED" "$APPIMAGE"

  if [[ ! -x "$EXTRACTED/AppRun" ]]; then
    echo "Bambu Studio AppRun missing after AppImage extraction" >&2
    exit 4
  fi
fi

cat >"$WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
APP_RUN="$EXTRACTED/AppRun"
if command -v xvfb-run >/dev/null 2>&1 && [[ -z "\${DISPLAY:-}" ]]; then
  # Bambu/GLFW may prefer a runner-provided Wayland hint even though no usable
  # compositor exists. Force the virtual X11 display and software GL in
  # headless Actions/Codespaces.
  unset WAYLAND_DISPLAY
  export GDK_BACKEND=x11
  export SDL_VIDEODRIVER=x11
  export LIBGL_ALWAYS_SOFTWARE=1
  exec xvfb-run -a -s "-screen 0 1280x1024x24" "\$APP_RUN" "\$@"
fi
exec "\$APP_RUN" "\$@"
EOF
chmod +x "$WRAPPER"

echo "Installed: $WRAPPER"
echo "Version: $BAMBU_STUDIO_VERSION"
echo "Profile root:"
find "$EXTRACTED" -type d -path '*/resources/profiles/BBL' -print -quit
