"""Benchmark the can-crasher holder's manual and automatic FDM ramps.

Run from the repository root, for example::

    python -m experiments.can_crasher_fdm --implementation baseline --label baseline
    python -m experiments.can_crasher_fdm --implementation current --label final
    python -m experiments.can_crasher_fdm --implementation current --render \
        --baseline-output /tmp/b4dcad-fdm-experiment/baseline

The script writes one STL for each variant and a JSON report to the output
directory.  The construction is copied from homecad's can_crasher example;
the ``raw`` variant deliberately leaves both slot blocks unwarped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import types
from pathlib import Path

import numpy as np
from b4dcad import conic, cube, cylinder

DEFAULT_OUTPUT = Path("/tmp/b4dcad-fdm-experiment")


def get_holder(*, manual_warp: bool):
    """Build the holder, optionally applying the original manual warp."""
    d = 70
    h = 125
    thick = 6
    c1 = conic(h=h + thick, d1=d + thick * 2, d2=d + thick * 4)
    c2 = conic(h=h, d1=d, d2=d + thick * 2).align_to(c1, ">Z")

    c1a = cylinder(h=h + thick, d=d + thick * 2)
    c2a = cylinder(h=h, d=d).align_to(c1, ">Z")

    c3 = cylinder(thick, d / 1.7).align_to(c1, "<Z")
    c4 = conic(h=thick * 3, d1=d + thick * 4, d2=d + thick * 2)

    slot = 23

    def warp(point):
        x, y, z = point
        if x > 0 and z > 0:
            z -= slot
        elif x <= 0 and z <= 0:
            z += slot
        return (x, y, z)

    def slot_block(height):
        block = cube(x=slot, y=d + thick * 2, z=height)
        return block.warp(warp) if manual_warp else block

    b1 = slot_block(h - thick * 5).align_to(c4, ":>Z -X -Y", dy=thick * 2)
    b1a = slot_block(h + slot).align_to(c4, ":>Z -X -Y", dy=thick * 2)

    bs = b1
    bsa = b1a
    for angle in range(0, 360, 60):
        bs = bs + b1.rotate(z=angle)
        bsa = bsa + b1a.rotate(z=angle)

    o1 = c1 + c4 - c2 - bs
    o2 = c1a - c2a - bsa
    return o1 + o2 - c3


def load_baseline_fdmutil():
    """Load the committed FDM implementation without touching the worktree."""
    source = subprocess.check_output(
        ["git", "show", "fdeaf79:b4dcad/fdmutil.py"], text=True
    )
    module = types.ModuleType("b4dcad_fdmutil_baseline")
    sys.modules[module.__name__] = module
    exec(compile(source, "fdeaf79:b4dcad/fdmutil.py", "exec"), module.__dict__)
    return module


def measure(name, model, output_dir, overhang_fn):
    """Write a model and return geometry and timing metrics."""
    started = time.perf_counter()
    path = output_dir / f"{name}.stl"
    model.stl(path)
    export_ms = (time.perf_counter() - started) * 1000
    mesh = model.manifold.to_mesh()
    mesh_path = output_dir / f"{name}-mesh64.npz"
    vertices64 = np.asarray(mesh.vert_properties[:, :3], dtype=np.float64)
    triangles64 = np.asarray(mesh.tri_verts, dtype=np.int64)
    np.savez_compressed(mesh_path, vertices=vertices64, triangles=triangles64)

    # Use 45.01 for the angle threshold, then remove triangles on the build
    # plane itself; the latter are bed contact surfaces rather than free hangs.
    overhang = overhang_fn(model.manifold, angle=45.01)
    vertices = vertices64
    triangles = mesh.tri_verts[overhang.triangle_indices]
    centroids = vertices[triangles].mean(axis=1)
    z_min = model.bounding_box()[2]
    free_hanging = centroids[:, 2] > z_min + 1e-5
    volume = model.manifold.volume()
    components = model.decompose()
    return {
        "stl": str(path),
        "mesh_npz": str(mesh_path),
        "stl_bytes": path.stat().st_size,
        "triangles": model.num_tri(),
        "overhang_area": float(overhang.areas[free_hanging].sum()),
        "volume": volume,
        "components": len(components),
        "component_volumes": [part.manifold.volume() for part in components],
        "export_ms": export_ms,
    }


def run(output_dir: Path, label: str, implementation: str):
    output_dir.mkdir(parents=True, exist_ok=True)
    variants = {}
    baseline_fdmutil = load_baseline_fdmutil()
    from b4dcad.fdmutil import detect_overhangs as overhang_fn

    def auto_fix(model, mode):
        if implementation == "baseline":
            return type(model)(
                baseline_fdmutil.fix_horizontal_overhangs(
                    model.manifold, angle=45, mode=mode
                )
            )
        return model.fix_horizontal_overhangs(angle=45, mode=mode)

    for name, builder in (
        ("raw", lambda: get_holder(manual_warp=False)),
        ("manual", lambda: get_holder(manual_warp=True)),
    ):
        started = time.perf_counter()
        model = builder()
        build_ms = (time.perf_counter() - started) * 1000
        metrics = measure(name, model, output_dir, overhang_fn)
        metrics["build_ms"] = build_ms
        metrics["total_ms"] = build_ms + metrics["export_ms"]
        variants[name] = metrics

    started = time.perf_counter()
    raw = get_holder(manual_warp=False)
    raw_build_ms = (time.perf_counter() - started) * 1000
    for mode in ("cut", "add"):
        fix_samples = []
        model = None
        for _ in range(5):
            started = time.perf_counter()
            candidate = auto_fix(raw, mode)
            # Manifold booleans are lazy; include evaluation in the fix timing.
            candidate.manifold.volume()
            fix_samples.append((time.perf_counter() - started) * 1000)
            model = candidate
        fix_ms = sorted(fix_samples)[len(fix_samples) // 2]
        name = f"auto_{mode}"
        metrics = measure(name, model, output_dir, overhang_fn)
        metrics["raw_build_ms"] = raw_build_ms
        metrics["fix_ms"] = fix_ms
        metrics["fix_ms_samples"] = fix_samples
        metrics["build_ms"] = raw_build_ms + fix_ms
        metrics["total_ms"] = metrics["build_ms"] + metrics["export_ms"]
        variants[name] = metrics

    report = {
        "label": label,
        "angle": 45.01,
        "implementation": implementation,
        "output_dir": str(output_dir),
        "variants": variants,
    }
    report_path = output_dir / f"{label}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return report


def render_comparison(baseline_dir, final_dir, output):
    """Render both four-variant mesh sets from one fixed camera."""
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    def read(path):
        data = np.load(path)
        return data["vertices"][data["triangles"]]

    fig = plt.figure(figsize=(16, 8), constrained_layout=True)
    for row, (directory, label) in enumerate(
        ((baseline_dir, "baseline"), (final_dir, "final"))
    ):
        for col, name in enumerate(("raw", "manual", "auto_cut", "auto_add")):
            triangles = read(directory / f"{name}-mesh64.npz")
            cross = np.cross(
                triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
            )
            lengths = np.linalg.norm(cross, axis=1)
            nz = np.divide(
                cross[:, 2], lengths, out=np.zeros_like(lengths), where=lengths > 1e-12
            )
            angles = np.degrees(np.arcsin(np.clip(-nz, 0, 1)))
            bed = triangles[:, :, 2].mean(1) <= triangles[:, :, 2].min() + 1e-5
            hanging = (angles > 45.01) & (nz < 0) & ~bed
            light = np.clip(
                0.35 + 0.65 * (cross[:, 2] / np.maximum(lengths, 1e-12) * 0.5 + 0.5),
                0,
                1,
            )
            colors = np.column_stack(
                (0.72 * light, 0.74 * light, 0.78 * light, np.ones(len(triangles)))
            )
            colors[hanging] = np.column_stack(
                (
                    0.9 * light[hanging],
                    0.18 * light[hanging],
                    0.08 * light[hanging],
                    np.ones(hanging.sum()),
                )
            )
            ax = fig.add_subplot(2, 4, row * 4 + col + 1, projection="3d")
            ax.add_collection3d(
                Poly3DCollection(triangles, facecolors=colors, edgecolors="none")
            )
            points = triangles.reshape(-1, 3)
            low, high = points.min(0), points.max(0)
            center, span = (low + high) / 2, (high - low).max()
            ax.set_xlim(center[0] - span / 2, center[0] + span / 2)
            ax.set_ylim(center[1] - span / 2, center[1] + span / 2)
            ax.set_zlim(low[2], low[2] + span)
            ax.set_box_aspect((1, 1, 1))
            ax.view_init(elev=24, azim=-58)
            ax.set_axis_off()
            area = float((0.5 * lengths)[hanging].sum())
            ax.set_title(f"{name}\n{label}: {area:.1f} mm² overhang", fontsize=10)
    fig.suptitle("Can-crasher FDM — red: free-hanging faces >45.01°")
    fig.savefig(output, dpi=180)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label")
    parser.add_argument(
        "--implementation", choices=("baseline", "current"), default="current"
    )
    parser.add_argument("--render", action="store_true")
    parser.add_argument(
        "--baseline-output", type=Path, default=DEFAULT_OUTPUT / "baseline"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or DEFAULT_OUTPUT / args.implementation
    run(output, args.label or args.implementation, args.implementation)
    if args.render:
        render_comparison(
            args.baseline_output, output, output / "overhang-comparison.png"
        )


if __name__ == "__main__":
    main()
