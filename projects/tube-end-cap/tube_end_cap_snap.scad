/*
  Snap-lock cap for round steel tube with TWO opposed retaining holes.
  User measured tube ID = 35mm and nearest hole edge 9.5mm from rim.
  Tube OD and hole length/width remain unverified.
  The cap and tabs must not be used as safety-critical structural fittings.
  Print flange down, tube insert up, no supports; PETG or ASA.
*/
tube_inner_diameter = 35.0;        // user measured
tube_outer_diameter = 40.0;        // PHOTO ESTIMATE - verify
radial_clearance = 0.25;
flange_overhang = 2.0;
flange_thickness = 3.0;
insertion_depth = 30.0;            // taller than old 18mm sleeve
sleeve_wall = 2.0;
entry_chamfer_height = 2.0;
// Axial distance from cut tube rim to NEAREST edge of existing hole.
// 9.5mm is the START of the hole (not its centre), user-measured.
hole_start_from_rim = 9.5;
latch_footprint_width = 4.0;
latch_radial_engagement = 0.60;
latch_shoulder_inset = 0.4; // shoulder just inside near edge of hole
latch_flat_length = 1.5; // spans 9.9mm to 11.4mm from rim
latch_ramp_length = 3.5;
snap_angle = 0;                   // rotate to align to opposed holes
flex_arm_wall = 1.2;
flex_arm_half_width = 5.0;
slot_width = 1.2;
slot_start_from_rim = 1.0;
mode = "cap"; // [cap,test]
$fn = 128;
assert(tube_inner_diameter > 2*sleeve_wall + 12);
assert(tube_outer_diameter > tube_inner_diameter);
assert(radial_clearance > 0 && radial_clearance < 1);
assert(flex_arm_wall > 0.8 && flex_arm_wall < sleeve_wall);
assert(insertion_depth > hole_start_from_rim + latch_shoulder_inset + latch_flat_length + latch_ramp_length + 2);
assert(hole_start_from_rim > slot_start_from_rim + 4);
assert(latch_shoulder_inset >= 0 && latch_shoulder_inset <= 1.5);

plug_od = tube_inner_diameter - 2*radial_clearance;
plug_r = plug_od/2;
flange_od = tube_outer_diameter + 2*flange_overhang;
cap_depth = mode == "test" ? max(21,hole_start_from_rim+latch_shoulder_inset+latch_flat_length+latch_ramp_length+3) : insertion_depth;
catch_radius = tube_inner_diameter/2 + latch_radial_engagement;
shoulder_z = hole_start_from_rim + latch_shoulder_inset;
flat_end_z = shoulder_z + latch_flat_length;
ramp_end_z = flat_end_z + latch_ramp_length;

// Radial X/axial Z wedge, 4mm tangential width.
// Near-rim shoulder resists pulling; long nose-facing ramp compresses tongue.
module single_snap_latch() {
    translate([0,latch_footprint_width/2,flange_thickness])
      rotate([90,0,0])
        linear_extrude(height=latch_footprint_width)
          polygon(points=[
            [plug_r-1.6,shoulder_z],
            [catch_radius,shoulder_z],
            [catch_radius,flat_end_z],
            [plug_r-0.10,ramp_end_z],
            [plug_r-1.6,ramp_end_z]
          ]);
}
module cap() {
    difference() {
      union() {
        cylinder(d1=flange_od-0.6,d2=flange_od,h=0.6);
        translate([0,0,0.6])
          cylinder(d=flange_od,h=flange_thickness-0.6);
        translate([0,0,flange_thickness])
          cylinder(d=plug_od,h=cap_depth-entry_chamfer_height);
        translate([0,0,flange_thickness+cap_depth-entry_chamfer_height])
          cylinder(d1=plug_od,d2=plug_od-1,h=entry_chamfer_height);
        rotate([0,0,snap_angle])
          for(a=[0,180]) rotate([0,0,a]) single_snap_latch();
      }
      // Sleeve hollow, solid flange face.
      translate([0,0,flange_thickness])
        cylinder(d=plug_od-2*sleeve_wall,h=cap_depth+0.3);
      // Thinner sections inside each snap tongue allow inward flex.
      rotate([0,0,snap_angle])
        for(a=[0,180]) rotate([0,0,a])
          intersection() {
            translate([0,0,flange_thickness+slot_start_from_rim])
              cylinder(d=plug_od-2*flex_arm_wall,
                       h=cap_depth-slot_start_from_rim+0.5);
            translate([8,-flex_arm_half_width-slot_width,flange_thickness])
              cube([25,2*(flex_arm_half_width+slot_width),cap_depth+1]);
          }
      // Four lengthwise cuts isolate two tongues (attached at flange root).
      rotate([0,0,snap_angle])
        for(a=[0,180]) rotate([0,0,a])
          for(side=[-1,1])
            translate([10,
                       side*(flex_arm_half_width+slot_width/2)-slot_width/2,
                       flange_thickness+slot_start_from_rim])
              cube([17,slot_width,cap_depth-slot_start_from_rim+0.5]);
    }
}
cap();
