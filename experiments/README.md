# FDM ramp experiment

The holder fixture is copied from `homecad/src/misc/can_crasher.py`.
`raw` removes both slot-block warps; `manual` preserves them. No homecad files
are modified. Baseline correction is loaded from commit `fdeaf79`.

Run from the repository root with b4dcad installed:

```sh
python -m experiments.can_crasher_fdm --implementation baseline
python -m experiments.can_crasher_fdm --implementation current --render
python -m experiments.fdm_scaling
```

Rendering additionally requires matplotlib. Outputs (STL, mesh64 NPZ, JSON,
and the comparison PNG) go to `/tmp/b4dcad-fdm-experiment/`. Use `--output` and
`--baseline-output` to change the holder output locations.

## Observations, 2026-09-05

One local run, five repetitions per correction, median elapsed time including
lazy boolean evaluation. Timing varies with machine load. Both implementations
use the same current detector, a 45.01-degree threshold, and exclude bed contact.

| Holder variant | Elevated overhang area (mm²) | Fix time (ms) | Triangles |
| --- | ---: | ---: | ---: |
| Raw | 843.046 | — | 1822 |
| Manual | 0 | — | 1752 |
| Baseline cut | 787.000 | 14.9 | 1936 |
| Baseline add | 792.386 | 15.7 | 1938 |
| Current cut | <0.000001 | 35.1 | 3322 |
| Current add | <0.000001 | 36.9 | 3006 |

The baseline skips five of the six slots because upward ray hits are at
different heights. The current implementation processes all six, so holder
runtimes compare different amounts of completed work. Current cut and add each
produce one connected component; baseline cut/add also contain tiny detached
shells. The current holder uses more triangles, despite avoiding per-triangle
CSG during construction.

For a separate six-slot tube with 1024 circular segments (12776 input triangles),
both algorithms process the same slots:

| Mode | Baseline (ms) | Current (ms) |
| --- | ---: | ---: |
| Cut | 521.9 | 162.4 |
| Add | 633.7 | 175.7 |

This is a geometry experiment, not a slicer/support-volume or print validation.
Local face angles do not establish support continuity, strength, or minimum
wall thickness. The algorithm still skips holed bottom regions and does not
check whether the supporting wall extends low enough. The manual fixture also
changes lower slot boundaries, so it is a visual reference rather than an
identical target shape for automatic correction.
