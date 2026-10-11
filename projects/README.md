# 3D-print projects

This directory collects reusable, parametric 3D-print designs independently of the Windsor Slicer service implementation.

Each project goes in its own `projects/<name>/` directory, with:
- `README.md` — purpose, measurements and their provenance, material/print settings, build instructions and fit-test result.
- Editable source files such as `.scad` — source of truth.
- Optional `generate.sh` — reproducible output of STL/3MF from source.
- Generated STL/3MF files in a local `dist/` folder (normally ignored unless a release artifact is intentionally needed).

For new designs, add a model to the repository-root `.windsor-slicer.yaml` so Windsor Slicer can prepare it when this repo is allowlisted. Keep sliced, printer-specific files transient.

## Projects

| Project | Description | Physical fit |
| --- | --- | --- |
| [Tube end cap](tube-end-cap/) | 35 mm inner-diameter steel-tube push-in cap with spring catches for holes starting 9.5 mm from rim | **User confirmed** |
