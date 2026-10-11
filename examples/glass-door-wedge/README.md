# Two-sided glass-door threshold ramps

Follow-up to [issue #17](https://github.com/antonio1000homens/windsor-slicer/issues/17) and merged PR #20.

The glass is confirmed to be **8 mm thick**, and the design now has **matching sloped ramps on BOTH sides**. Each ramp rises to the same height beside the glass. There is no longer a short retaining lip.

This is a **50 mm section for fit-testing**, not a full-width door threshold. A continuous solid bottom wedge supports the glass. The 10 mm height is measured from the **seated bottom edge of the glass** to the top of each ramp: floor underside Z=0; glass sits on the 2 mm base at Z=2 mm; both crests reach Z=12 mm.

## Dimensions, in mm

| Parameter | Default | What it controls |
| --- | ---: | --- |
| glass_thickness | **8** | Confirmed nominal glass thickness |
| fit_clearance | 0.2 | Gap on each side; slot inside width = 8.4 mm |
| base_thickness | **2** | Thickness under the seated glass |
| rail_height | **10** | Height of each ramp crest above the glass seat |
| ramp_run | 30 | Horizontal rise/run on EACH side (adjust to change slope) |
| wall_thickness | 1.6 | Crest support on each side of glass |
| toe_thickness | 0.8 | Thickness at the outer edge of each ramp |
| entry_chamfer | 0.2 | Lead-in on glass-facing tops |
| segment_length | 50 | Test piece length along the door |

Cross-section looking along the length of the door (not to scale):

        left ramp      8 mm glass      right ramp
                        |       |
                    ____|       |____  <-- 10 mm above glass bottom
                   /    |       |    \
                  /     |       |     \
                 /      |       |      \
                /       |_______|       \
          _____/_________________________\_____
              30 mm       2 mm       30 mm
               run         base        run

Model axes: X across glass, Y along door, Z vertical. Default STL bounding box: **71.6 × 50 × 12 mm**. Flat bottom at Z=0 for bed adhesion.

## OpenSCAD and verification

From examples/glass-door-wedge:

    openscad --export-format binstl -o glass_door_wedge.stl glass_door_wedge.scad
    python3 verify.py

Change test length to 100 mm and make shallower ramps:

    openscad --export-format binstl -D 'segment_length=100' -D 'ramp_run=40' \
      -o glass_door_wedge_100mm.stl glass_door_wedge.scad

The verifier checks STL dimensions, bed orientation, both ramp profile vertices, mirror symmetry, manifold edges, committed STL agreement with a fresh OpenSCAD export, and a non-default parameter variant.

## Print plan and restrictions

Start with Bambu Lab H2D, 0.4 mm nozzle, 0.20 mm Standard, **PETG Basic**, **Textured PEI Plate**, support off. Verify the preview and line widths around the thin outer toes. Commit canonical SCAD/STL; treat sliced 3MF as temporary. No printer job has been started.

Windsor Slicer can stage only allowlisted consumer repositories (currently led and windsor-slicer-fixture), not windsor-slicer itself. For a live MCP slicing test copy this SCAD into an allowlisted consumer repo with a manifest entry like this:

    version: 2
    models:
      glass-door-threshold:
        source: examples/glass-door-wedge/glass_door_wedge.scad
        generator: openscad
        output: glass_door_wedge.stl
        variants:
          petg-no-support:
            machine_profile: "Bambu Lab H2D 0.4 nozzle"
            process_profile: "0.20mm Standard @BBL H2D"
            filament_profile: "Bambu PETG Basic @BBL H2D 0.4 nozzle"
            bed_type: "Textured PEI Plate"
            support_mode: off

This example does not authorize changing the slicer allowlist.

**Before installation:** Confirm at least **2 mm clearance under the glass** and adequate room on both sides. The 30 mm ramp run is a configurable starting assumption, not a confirmed door measurement. Print a short sample, remove burrs and check glass-safe fit; do not force a clip against a tempered glass edge or modify any fire-rated door without manufacturer approval.
