# Glass-door bottom wedge and 10 mm retaining rail

Implements [issue #17](https://github.com/antonio1000homens/windsor-slicer/issues/17).

This is a **50 mm long test section**, not a full-width door strip. It is an asymmetric open-top U-channel: a tall rail rises **10 mm above the seated bottom of the glass**, with a shorter 3 mm lip on the opposite side. The floor-facing base is **2 mm thick**, with a tapered toe projecting **2 mm** beyond the short lip. No screws, adhesive or glass drilling are required.

## Dimensions

| OpenSCAD parameter | Default | Meaning |
| --- | --- | --- |
| glass_thickness | **0.8 mm** | As supplied, **not confirmed**; check if this is actually **8 mm** |
| fit_clearance | 0.2 mm | Added on **each side** of glass; channel inside width = glass thickness + 0.4 mm |
| base_thickness | **2 mm** | Height below the seated glass; check door floor clearance |
| wedge_depth | **2 mm** | Horizontal tapered toe projection beyond the low lip |
| rail_height | **10 mm** | Upright height *above the bottom of the glass*, not overall part height |
| return_lip_height | 3 mm | Opposite retaining lip |
| wall_thickness | 1.2 mm | Three nominal 0.4 mm nozzle line widths |
| segment_length | 50 mm | Test part length along the door; configurable |
| entry_chamfer | 0.2 mm | Lead-in to reduce corner interference |

**Axes:** X = across the glass, Y = along the door, Z = vertical from floor. Base underside is Z=0; the glass sits on Z=2 mm and the tall rail ends at Z=12 mm. For the stated 0.8 mm input, the channel is 1.2 mm wide internally; nominal STL bounding box = **5.6 × 50 × 12 mm**.

Cross-section sketch (not to scale):

    Tall rail (10 mm from glass seat)        Short lip (3 mm)
      +--+                    open top        +--+
      |  |     glass                           |  |
      |  |      ||                             |  |
      |  +------+|------------+----------------+
      +-----------------------+\
                               \ tapered toe
                          2 mm projection; 2 mm bottom clearance

**Measurement warning:** 0.8 mm is unusually thin glass for a door; do not force the part onto thicker glass. Measure the thickness and check door movement/ground clearance before using it. Remove any print burrs and avoid sharp contact with glass. Do not use on safety-critical or fire-rated doors without checking the manufacturer's instructions.

## Regenerate

Run from this folder:

    openscad --export-format binstl -o glass_door_wedge.stl glass_door_wedge.scad

For glass that actually measures 8 mm:

    openscad --export-format binstl -D 'glass_thickness=8' -o glass_door_wedge_8mm.stl glass_door_wedge.scad

For a 100 mm section:

    openscad --export-format binstl -D 'segment_length=100' -o glass_door_wedge_100mm.stl glass_door_wedge.scad

To verify the committed STL and both glass thickness variants, including mesh closure and dimensions:

    python3 verify.py

## Print and Windsor Slicer

The STL is oriented flat with **Z=0 on the print bed**, not upright along its 50 mm extrusion axis. Suggested starting settings: Bambu Lab H2D, 0.4 mm nozzle, 0.20 mm Standard process, **PETG Basic**, **Textured PEI Plate**, support off. Validate the slicer preview before printing; the geometry has been tested for watertightness, but no H2D slice or physical snap-fit test is implied.

Windsor Slicer currently stages **allowlisted consumer repositories**, not the slicer-service repository itself. To use the MCP slicer, first copy the SCAD to an allowlisted consumer repository and declare its source/output in that repository's .windsor-slicer.yaml:

    version: 2
    models:
      glass-door-wedge:
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

The manifest above is an **example only**; it is not placed in the service repo as if that repo were a staged consumer. Commit the SCAD + canonical STL here, but leave printer-specific sliced 3MF artifacts uncommitted. Print the short sample and establish real-world fit before changing segment_length.
