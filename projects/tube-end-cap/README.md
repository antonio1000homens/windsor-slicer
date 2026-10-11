# Tube end cap — 35 mm internal snap fit

**Status: physically fitted.** On 11 October 2026, the user confirmed that the **9.5 mm hole-start / 35 mm inner diameter** snap-lock version fits the real steel tube.

This is the canonical version. Earlier prototypes with a photo-estimated 37.4 mm bore were too large; earlier 12 mm and 16 mm *hole-centre* variants have been superseded. This model is a non-load-bearing protective/dust cap and must not be used as a structural component.

## Key dimensions (mm)

| Measurement | Default | Provenance |
| --- | ---: | --- |
| Tube inside diameter | **35.0** | Measured |
| Distance from cut tube rim to **start (near edge) of retaining hole** | **9.5** | Measured |
| Plug outside diameter | 34.5 | 0.25 mm radial clearance |
| Plug insertion depth | 30.0 | CAD |
| Whole cap height | 33.0 | 30 mm insert + 3 mm face |
| Snap shoulder begins / ends | 9.9 / 11.4 from rim | CAD; 0.4 mm inset from opening |
| Snap latch engagement past bore | 0.6 | CAD |
| Snap tab tangential width | 4.0 | CAD |
| Tube outside diameter | **40.0** | Photo estimate, **not measured** |
| Flange outside diameter | 44.0 | CAD; depends on estimated tube OD |

Two opposed, separately flexible tongues run in axial relief slots and snap into the tube's side holes. Align the tabs to the side holes when fitting, and depress both tabs through the holes for removal. The physical test confirmed fit but does not verify long-term ageing, repeated snap-cycle durability or weather resistance.

## Files

- [`tube_end_cap_snap.scad`](tube_end_cap_snap.scad) — editable CAD source, with tunable bore, hole start, flange and flexure parameters.
- [`generate.sh`](generate.sh) — reproducibly generates the full STL and a shorter test piece. Generated binary files belong in `dist/` and are ignored by Git.

Run from this directory (requires OpenSCAD installed):

```bash
bash generate.sh
```

Or directly:

```bash
openscad -o tube_end_cap_snap_9p5mm_from_edge.stl tube_end_cap_snap.scad
openscad -D 'mode="test"' -o tube_end_cap_snap_9p5mm_fit_test.stl tube_end_cap_snap.scad
```

When modifying a dimension, reprint the short fit model first and verify both latches engage and release without forcing the tube or plastic.

## Slicer verification

The identical geometry was previously generated in the allowlisted `windsor-slicer-fixture` repo and sliced by Windsor Slicer using BambuStudio `v02.08.02.61`, H2D 0.4 mm nozzle, 0.20 mm standard layers, **Bambu PETG Basic**, Textured PEI, **supports off**:

- Fixture commit: `ed0c06cabf6edc12c1077c8fbf2a9f3802a24245`.
- Result: `ready_for_print=true`, `slicer_exit=0`; no error/warning categories.
- The STL was a watertight, single-body mesh.

A root `.windsor-slicer.yaml` declares this model for future MCP slicing. **Current Windsor Slicer workspace allowlist must explicitly include `antonio1000homens/windsor-slicer` before self-repository staging works**. Existing hosted fixture validation remains the current proven run.

## Origin and project tracking

Migrated from [windsor-app issue #1916](https://github.com/antonio1000homens/windsor-app/issues/1916) and [PR #1917](https://github.com/antonio1000homens/windsor-app/pull/1917), with ongoing work tracked at [windsor-slicer issue #18](https://github.com/antonio1000homens/windsor-slicer/issues/18).
