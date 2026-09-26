#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <runtime/inputs/... model.stl|model.3mf> [output-dir]" >&2
}

[[ $# -ge 1 && $# -le 2 ]] || { usage; exit 2; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INPUT_RAW="$1"
INPUT="$(python3 - "$ROOT_DIR" "$INPUT_RAW" <<'PY'
from pathlib import Path
import os
import sys
root=Path(sys.argv[1]).resolve()
path=(root / sys.argv[2]).resolve() if not Path(sys.argv[2]).is_absolute() else Path(sys.argv[2]).resolve()
try:
    rel=path.relative_to(root)
except ValueError:
    raise SystemExit("input escapes repository root")
allowed_roots = [
    ((Path(os.environ["SLICER_RUNTIME_ROOT"]).expanduser() if os.environ.get("SLICER_RUNTIME_ROOT") else root / "runtime") / "inputs").resolve(),
]
try:
    allowed = any(path.is_relative_to(parent) for parent in allowed_roots)
except AttributeError:
    allowed = False
    for parent in allowed_roots:
        try:
            path.relative_to(parent)
            allowed = True
            break
        except ValueError:
            continue
if not allowed:
    raise SystemExit(
        "input must be under runtime/inputs/"
    )
if path.suffix.lower() not in {".stl", ".3mf"}:
    raise SystemExit("input must be STL or 3MF")
if not path.is_file():
    raise SystemExit(f"input does not exist: {rel}")
print(path)
PY
)"

STEM="$(basename "$INPUT")"
STEM="${STEM%.*}"
OUT_DIR="${2:-$ROOT_DIR/artifacts/slicer/$STEM}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

BAMBU_STUDIO_BIN="${BAMBU_STUDIO_BIN:-$ROOT_DIR/.tools/bin/bambu-studio}"
if [[ ! -x "$BAMBU_STUDIO_BIN" ]]; then
  if [[ "${SLICER_AUTO_INSTALL:-1}" != "1" ]]; then
    echo "Bambu Studio is not installed: $BAMBU_STUDIO_BIN" >&2
    exit 3
  fi
  "$ROOT_DIR/scripts/slicer/install-bambu-studio.sh"
fi

PROFILE_ROOT="${BAMBU_PROFILE_ROOT:-}"
if [[ -z "$PROFILE_ROOT" ]]; then
  PROFILE_ROOT="$(find "$ROOT_DIR/.tools/bambu-studio" -type d -path '*/resources/profiles/BBL' -print -quit 2>/dev/null || true)"
fi
if [[ -z "$PROFILE_ROOT" || ! -d "$PROFILE_ROOT" ]]; then
  echo "Unable to find Bambu Studio BBL profile root; set BAMBU_PROFILE_ROOT" >&2
  exit 4
fi

PROFILE_DIR="$OUT_DIR/profiles"
rm -rf "$PROFILE_DIR"
python3 "$ROOT_DIR/scripts/slicer/flatten-bambu-profile.py"   --profile-root "$PROFILE_ROOT"   --machine "${SLICER_MACHINE_PROFILE:-Bambu Lab H2D 0.4 nozzle}"   --process "${SLICER_PROCESS_PROFILE:-0.20mm Standard @BBL H2D}"   --filament "${SLICER_FILAMENT_PROFILE:-Bambu PLA Basic @BBL H2D}"   --output-dir "$PROFILE_DIR"

MACHINE="$PROFILE_DIR/machine.json"
PROCESS="$PROFILE_DIR/process.json"
FILAMENT="$PROFILE_DIR/filament.json"
OUTPUT_3MF="$OUT_DIR/$STEM.sliced.3mf"
LOG="$OUT_DIR/slicer.log"
RESULT="$OUT_DIR/result.json"
rm -f "$OUTPUT_3MF" "$LOG" "$RESULT"

args=(
  "$BAMBU_STUDIO_BIN"
  --arrange 1
  --load-settings "$MACHINE;$PROCESS"
  --load-filaments "$FILAMENT"
  --slice 0
  --debug 2
  --outputdir "$OUT_DIR"
  # Bambu resolves --export-3mf relative to --outputdir; passing an absolute
  # path causes it to concatenate the two paths.
  --export-3mf "$STEM.sliced.3mf"
)

# Preserve the CAD/STL orientation by default so CI catches the same unsupported
# geometry the designer intended to print. Codespaces experiments can opt in.
if [[ "${SLICER_ORIENT:-0}" == "1" ]]; then
  args+=(--orient)
fi

if [[ -n "${SLICER_BED_TYPE:-}" ]]; then
  args+=("--curr-bed-type=${SLICER_BED_TYPE}")
fi

args+=("$INPUT")

echo "Slicing: $INPUT"
echo "Machine: ${SLICER_MACHINE_PROFILE:-Bambu Lab H2D 0.4 nozzle}"
echo "Process: ${SLICER_PROCESS_PROFILE:-0.20mm Standard @BBL H2D}"
echo "Filament: ${SLICER_FILAMENT_PROFILE:-Bambu PLA Basic @BBL H2D}"
echo "Auto-orient: ${SLICER_ORIENT:-0}"

started_ms="$(date +%s%3N)"
set +e
"${args[@]}" 2>&1 | tee "$LOG"
slicer_rc=${PIPESTATUS[0]}
set -e
finished_ms="$(date +%s%3N)"
elapsed_ms=$((finished_ms - started_ms))

set +e
python3 "$ROOT_DIR/scripts/slicer/classify-slicer-log.py"   --log "$LOG"   --output "$RESULT"   --slicer-exit "$slicer_rc"   --artifact "$OUTPUT_3MF"
validation_rc=$?
set -e

python3 - "$RESULT" "$INPUT" "$elapsed_ms" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text())
elapsed_ms = int(sys.argv[3])
data["input"] = sys.argv[2]
data["slice_milliseconds"] = elapsed_ms
data["slice_seconds"] = round(elapsed_ms / 1000, 3)
path.write_text(json.dumps(data, indent=2) + "\n")
PY

echo "Slice time: ${elapsed_ms}ms"
echo "Result: $RESULT"
if [[ -f "$OUTPUT_3MF" ]]; then
  echo "Artifact: $OUTPUT_3MF ($(du -h "$OUTPUT_3MF" | awk '{print $1}'))"
fi

exit "$validation_rc"
