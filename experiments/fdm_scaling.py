"""Compare FDM algorithms on increasingly fine six-slot tubes.

Run from the repository root: python -m experiments.fdm_scaling
"""

import json
import statistics
import time
from pathlib import Path

from manifold3d import Manifold

from b4dcad.fdmutil import fix_horizontal_overhangs
from experiments.can_crasher_fdm import load_baseline_fdmutil


def main():
    results = []
    baseline = load_baseline_fdmutil().fix_horizontal_overhangs
    for segments in (64, 256, 1024):
        part = Manifold.cylinder(20, 10, circular_segments=segments)
        part -= Manifold.cylinder(20, 8, circular_segments=segments)
        for angle in range(0, 360, 60):
            slot = Manifold.cube((5, 12, 10)).translate((-2.5, 0, 5))
            part -= slot.rotate((0, 0, angle))
        part.volume()
        for name, fix in (
            ("baseline", baseline),
            ("current", fix_horizontal_overhangs),
        ):
            for mode in ("cut", "add"):
                samples = []
                for _ in range(5):
                    started = time.perf_counter()
                    result = fix(part, mode=mode)
                    result.volume()  # Include evaluation of lazy booleans.
                    samples.append((time.perf_counter() - started) * 1000)
                results.append(
                    dict(
                        segments=segments,
                        input_triangles=part.num_tri(),
                        implementation=name,
                        mode=mode,
                        median_ms=statistics.median(samples),
                        samples_ms=samples,
                        output_triangles=result.num_tri(),
                    )
                )
    output = Path("/tmp/b4dcad-fdm-experiment/scaling.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, indent=2) + "\n")
    print(output.read_text())


if __name__ == "__main__":
    main()
