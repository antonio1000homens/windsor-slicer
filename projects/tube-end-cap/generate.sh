#!/usr/bin/env bash
set -euo pipefail

# Run this file from any working directory: bash projects/tube-end-cap/generate.sh
cd "$(dirname "$0")"
command -v openscad >/dev/null || { echo "OpenSCAD not installed" >&2; exit 1; }
mkdir -p dist

openscad -o dist/tube_end_cap_snap_9p5mm_from_edge.stl tube_end_cap_snap.scad
openscad -o dist/tube_end_cap_snap_9p5mm_fit_test.stl -D 'mode="test"' tube_end_cap_snap.scad

echo "Generated STLs in $(pwd)/dist/"
