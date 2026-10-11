// Two-sided glass door threshold ramps. All dimensions are in millimetres.
// X = across glass; Y = along door; Z = height above the floor.
// Default glass is confirmed 8mm (issue #17 follow-up).
// Example: openscad -D 'segment_length=100' -o wedge_100mm.stl glass_door_wedge.scad

glass_thickness = 8; // confirmed thickness of the glass door
fit_clearance = 0.2; // clearance on EACH side of the glass
base_thickness = 2;   // space taken beneath the glass, from floor to glass bottom
rail_height = 10;     // ramps meet the glass 10mm ABOVE the bottom of the glass
ramp_run = 30;        // horizontal slope length on EACH side of the glass
wall_thickness = 1.6; // material between the top of each ramp and channel inner face
toe_thickness = 0.8;  // printable material remaining at the outer tips of the ramps
segment_length = 50;  // test section along the door (not a full-width threshold)
entry_chamfer = 0.2;  // lead-in for the top of the two inner faces

// Cross section: two symmetric triangular ramps flanking an 8mm glass slot.
// Bottom of glass at Z=2; ramps/retaining sides reach Z=12 (10mm above glass seat).
channel_width = glass_thickness + 2 * fit_clearance;
rail_top = base_thickness + rail_height;
left_wall_outer = -wall_thickness;
right_wall_outer = channel_width + wall_thickness;
left_toe = left_wall_outer - ramp_run;
right_toe = right_wall_outer + ramp_run;

assert(glass_thickness > 0, "glass_thickness must be positive");
assert(fit_clearance >= 0, "fit_clearance must be non-negative");
assert(base_thickness > 0 && rail_height > 0, "base and rail heights must be positive");
assert(ramp_run > 0, "ramp_run must be positive");
assert(toe_thickness > 0 && toe_thickness < rail_top,
       "toe_thickness must be positive and below the crest");
assert(wall_thickness >= 1.2 && entry_chamfer >= 0 && entry_chamfer < wall_thickness,
       "wall must remain printable after chamfer");
assert(entry_chamfer < rail_height, "chamfer must not cross glass seat");
assert(segment_length > 0, "segment_length must be positive");

// A single 2D cross-section avoids seams between base, walls, and ramps.
// rotate converts extruded model Y direction to the along-door axis, leaving Z=0
// flat on the build plate.
translate([0, segment_length, 0])
rotate([90, 0, 0])
linear_extrude(height = segment_length, convexity = 10)
polygon(points = [
    [left_toe, 0],
    [right_toe, 0],
    [right_toe, toe_thickness],
    [right_wall_outer, rail_top],            // right ramp rises towards glass
    [channel_width + entry_chamfer, rail_top],
    [channel_width, rail_top - entry_chamfer],
    [channel_width, base_thickness],         // inner right glass face
    [0, base_thickness],                     // horizontal glass seat
    [0, rail_top - entry_chamfer],           // inner left glass face
    [-entry_chamfer, rail_top],
    [left_wall_outer, rail_top],             // left ramp begins beside glass
    [left_toe, toe_thickness]
]);
