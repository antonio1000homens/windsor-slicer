"""Verify the committed wedge STL and symmetry of the two 8 mm glass ramps.

Usage: python3 examples/glass-door-wedge/verify.py
Requires only Python stdlib and OpenSCAD; no printer, API or extra pip packages.
"""

from collections import Counter
from pathlib import Path
import math
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
SCAD = HERE / "glass_door_wedge.scad"
COMMITTED_STL = HERE / "glass_door_wedge.stl"


def triangles(path: Path):
    data = path.read_bytes()
    if len(data) < 84:
        raise AssertionError(f"{path.name}: STL is too short")
    number, = struct.unpack_from("<I", data, 80)
    assert number > 0, f"{path.name}: empty STL"
    assert len(data) == 84 + number * 50, f"{path.name}: bad binary STL size"
    result = []
    for n in range(number):
        facet = struct.unpack_from("<12fH", data, 84 + n * 50)
        vertices = tuple(tuple(round(v, 3) for v in facet[i:i + 3])
                         for i in (3, 6, 9))
        assert all(math.isfinite(c) for v in vertices for c in v)
        assert len(set(vertices)) == 3, "degenerate facet"
        result.append(vertices)
    return result


def verify_mesh(mesh, *, glass=8, clearance=0.2, wall=1.6, ramp=30,
                base=2, rail=10, toe=0.8, length=50, chamfer=0.2):
    channel = glass + 2 * clearance
    peak = base + rail
    expected_width = channel + 2 * wall + 2 * ramp
    vertices = [v for face in mesh for v in face]
    lo = [min(v[i] for v in vertices) for i in range(3)]
    hi = [max(v[i] for v in vertices) for i in range(3)]
    dims = [hi[i] - lo[i] for i in range(3)]
    for actual, expected in zip(dims, (expected_width, length, peak)):
        assert abs(actual - expected) < 0.015, f"Incorrect bounds: {dims}"
    assert abs(lo[2]) < 0.015 and abs(lo[1]) < 0.015, "Incorrect print orientation"

    # The extruded profile is composed of mirrored left/right ramps including
    # the lead-in chamfers and crest walls; only the floor of the glass channel
    # is excluded from the mirror pair.
    cross_section = {(v[0], v[2]) for v in vertices}
    required = [
        (-wall - ramp, 0), (-wall - ramp, toe), (-wall, peak),
        (-chamfer, peak), (0, peak - chamfer), (0, base),
        (channel, base), (channel, peak - chamfer),
        (channel + chamfer, peak), (channel + wall, peak),
        (channel + wall + ramp, toe), (channel + wall + ramp, 0),
    ]
    for x, z in required:
        assert (round(x, 3), round(z, 3)) in cross_section, f"Missing profile vertex ({x}, {z})"
    for x, z in cross_section:
        if z == 0 and x not in (round(-wall-ramp, 3), round(channel+wall+ramp, 3)):
            continue
        mirror = (round(channel - x, 3), z)
        assert mirror in cross_section, f"Ramps are not mirror-symmetric: {x}, {z}"

    edges = Counter()
    for a, b, c in mesh:
        for first, second in ((a, b), (b, c), (c, a)):
            edges[tuple(sorted((first, second)))] += 1
    assert all(n == 2 for n in edges.values()), "Mesh has non-manifold or open edges"
    print(f"OK: {len(mesh)} triangles; size {tuple(round(d, 3) for d in dims)} mm; mirrored ramps; watertight")


def generate(path: Path, *options):
    cmd = ["openscad", "--export-format", "binstl", *options,
           "-o", str(path), str(SCAD)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return triangles(path)


def main():
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder)
        committed = triangles(COMMITTED_STL)
        fresh = generate(out / "fresh.stl")
        verify_mesh(committed)
        assert Counter(tuple(sorted(t)) for t in committed) == Counter(
            tuple(sorted(t)) for t in fresh), "STL differs from the OpenSCAD source"
        variant = generate(out / "6mm.stl", "-D", "glass_thickness=6",
                           "-D", "ramp_run=20", "-D", "segment_length=75")
        verify_mesh(variant, glass=6, ramp=20, length=75)
    print("PASS: default 8 mm STL matches SCAD, symmetric double ramps and 6 mm variant valid")


if __name__ == "__main__":
    main()
