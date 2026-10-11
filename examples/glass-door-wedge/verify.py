"""Verify the committed binary STL and parametric 8 mm glass variant.

Usage: python3 examples/glass-door-wedge/verify.py
Dependencies: Python standard library and the openscad executable.
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
    assert number > 0, f"{path.name}: empty model"
    assert len(data) == 84 + number * 50, f"{path.name}: not a valid binary STL"
    result = []
    for n in range(number):
        triangle = struct.unpack_from("<12fH", data, 84 + n * 50)
        vertices = tuple(tuple(round(v, 3) for v in triangle[i:i + 3])
                         for i in (3, 6, 9))
        assert all(math.isfinite(c) for v in vertices for c in v)
        assert len(set(vertices)) == 3, "degenerate facet"
        result.append(vertices)
    return result


def verify_mesh(mesh, expected_width):
    vertices = [v for face in mesh for v in face]
    lows = [min(v[i] for v in vertices) for i in range(3)]
    highs = [max(v[i] for v in vertices) for i in range(3)]
    dims = [hi - lo for lo, hi in zip(lows, highs)]
    for value, expected in zip(dims, (expected_width, 50, 12)):
        assert abs(value - expected) < 0.015, f"Incorrect bounds: {dims}"
    assert abs(lows[2]) < 0.015, f"Model does not lie flat on Z=0: {lows}"

    # An undirected manifold has exactly two triangles per edge, including end caps.
    edges = Counter()
    for a, b, c in mesh:
        for first, second in ((a, b), (b, c), (c, a)):
            edges[tuple(sorted((first, second)))] += 1
    assert all(n == 2 for n in edges.values()), "Mesh contains open/non-manifold edges"
    print(f"OK: {len(mesh)} facets; bounds {tuple(round(d, 3) for d in dims)} mm; watertight")


def generate(path: Path, *options):
    cmd = ["openscad", "--export-format", "binstl", *options,
           "-o", str(path), str(SCAD)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return triangles(path)


def main():
    with tempfile.TemporaryDirectory() as folder:
        output = Path(folder)
        committed = triangles(COMMITTED_STL)
        fresh = generate(output / "fresh.stl")
        verify_mesh(committed, 5.6)  # 0.8 mm glass + 0.4 mm clearance + walls + toe
        # STL headers and triangle ordering can vary between OpenSCAD versions.
        assert Counter(tuple(sorted(t)) for t in committed) == Counter(
            tuple(sorted(t)) for t in fresh), "Committed STL differs from SCAD source"
        verify_mesh(generate(output / "8mm.stl", "-D", "glass_thickness=8"), 12.8)
    print("PASS: committed STL and SCAD geometry agree; 8 mm variant valid")


if __name__ == "__main__":
    main()
