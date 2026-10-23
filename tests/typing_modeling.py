"""Static API regression checks: basedpyright --level error tests/typing_modeling.py."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing_extensions import assert_type
    from pathlib import Path
    import numpy as np
    from b4dcad import (
        Solid,
        Shape,
        cube,
        square,
        union,
        difference,
        intersection,
        loft,
        sweep,
        mesh,
        level_set,
        dxf,
        save_3mf,
        hull_points,
    )
    from b4dcad.contrib import extrude_edges

    s = cube(2.5, 3.2, 4.1)
    a = square(2.5, 3.2)
    assert_type(s.get_volume(), float)
    assert_type(s.split(s), tuple[Solid, Solid])
    assert_type(s.split(s.manifold), tuple[Solid, Solid])
    assert_type(hull_points([(0.0, 0.0, 0.0)]), Solid)
    assert_type(s.rotate(z=12.5, origin=(1.0, 2.0, 3.0)), Solid)
    assert_type(s.orient((1.0, 2.0, 3.0)), Solid)
    assert_type(s.section(origin=(0.0, 0.0, 0.5)), Shape)
    assert_type(a.rotate(12.5, origin=(1.0, 2.0)), Shape)
    assert_type(union(), Solid)
    assert_type(union(a, a), Shape)
    assert_type(union([a, a]), Shape)
    assert_type(union(s, s), Solid)
    assert_type(union([s, s]), Solid)
    assert_type(difference(a, a), Shape)
    assert_type(difference(s, [s]), Solid)
    assert_type(intersection(), Solid)
    assert_type(intersection(a, a), Shape)
    assert_type(intersection([s, s]), Solid)
    assert_type(loft([a, a], [0.0, 2.5]), Solid)
    assert_type(sweep(a, np.zeros((3, 3)), twist=12.5), Solid)
    assert_type(mesh(np.zeros((3, 3)), np.zeros((1, 3), dtype=int)), Solid)
    assert_type(
        level_set(lambda x, y, z: x + y + z, (0.0, 0.0, 0.0, 1.0, 1.0, 1.0), 0.25),
        Solid,
    )
    assert_type(dxf(data="", spline_tol=0.0025), Shape)
    assert_type(extrude_edges(a, 5.5, 0.25), Solid)
    assert_type(s.threemf(), bytes)
    assert_type(s.threemf(Path("part.3mf")), Solid)
    assert_type(save_3mf(None, [("part", s, (0.1, 0.2, 0.3))]), bytes)
    assert_type(save_3mf("part.3mf", [s]), None)
    assert_type(s.png("part.png", elev=12.5, max_edge=0.5), Solid)
