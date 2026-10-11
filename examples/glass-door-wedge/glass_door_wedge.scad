// Glass door bottom wedge / edge rail. All dimensions in millimetres.
// X = across glass, Y = along the door, Z = vertical from floor.
// Issue #17: 0.8 mm is the stated glass thickness, not a confirmed measurement.
// For 8 mm glass: openscad -D 'glass_thickness=8' -o wedge_8mm.stl glass_door_wedge.scad

glass_thickness = 0.8; // measured glass thickness, CHECK 0.8 vs 8 mm before use
fit_clearance = 0.2;   // allowance PER SIDE of glass
wedge_depth = 2;       // horizontal outward projection of tapered bottom wedge
base_thickness = 2;    // vertical clearance taken beneath the seated glass
rail_height = 10;      // tall rail above the glass seating surface
return_lip_height = 3; // low lip on opposite side for retention
wall_thickness = 1.2;  // three 0.4 mm nozzle widths, subject to slicer verification
segment_length = 50;   // short test part; set to actual door length after test fit
entry_chamfer = 0.2;   // small lead-in at each inner rail edge

// The door rests on Z=base_thickness between X=0 and X=channel_width.
channel_width = glass_thickness + 2 * fit_clearance;
outer_front = channel_width + wall_thickness;

assert(glass_thickness > 0, "glass_thickness must be positive")
assert(fit_clearance >= 0, "fit_clearance must be non-negative")
assert(wedge_depth > 0 && base_thickness > 0, "wedge/base dimensions must be positive")
assert(wall_thickness > entry_chamfer && wall_thickness >= 0.8,
       "walls must accommodate chamfer and at least two 0.4mm extrusion widths")
assert(rail_height > entry_chamfer && return_lip_height > entry_chamfer,
       "rail and lip must be taller than chamfer")
assert(segment_length > 0 && entry_chamfer >= 0,
       "segment length must be positive; chamfer cannot be negative")
translate([0, segment_length, 0])
rotate([90, 0, 0]) // Extruded door length lies on the bed (Y), not vertically (Z).
linear_extrude(height = segment_length, convexity = 10)
polygon(points = [
    [-wall_thickness, 0],
    [outer_front + wedge_depth, 0],          // outermost tapered toe, 2 mm projection
    [outer_front, base_thickness],
    [outer_front, base_thickness + return_lip_height],
    [channel_width + entry_chamfer, base_thickness + return_lip_height],
    [channel_width, base_thickness + return_lip_height - entry_chamfer],
    [channel_width, base_thickness],
    [0, base_thickness],
    [0, base_thickness + rail_height - entry_chamfer],
    [-entry_chamfer, base_thickness + rail_height],
    [-wall_thickness, base_thickness + rail_height]
]);
