"""Estimate FDM part material from horizontal cross-sections."""

from math import ceil, nextafter

from manifold3d import Error, JoinType, Manifold

LAYER_HEIGHT = 0.2
WALL_THICKNESS = 0.8
TOP_LAYERS = 5
BOTTOM_LAYERS = 3
INFILL = 0.15
PLA_DENSITY = 1.24
PETG_DENSITY = 1.27


def print_material_volume(solid: Manifold) -> float:
    """Return the estimated part-only extrusion volume in mm³."""
    if solid.status() != Error.NoError:
        raise ValueError("Print estimate requires a valid solid")
    if solid.is_empty():
        return 0.0

    _, _, bottom, _, _, top = solid.bounding_box()
    height = top - bottom
    layer_count = max(1, ceil(nextafter(height / LAYER_HEIGHT, 0)))
    last_thickness = height - (layer_count - 1) * LAYER_HEIGHT
    sections = [
        solid.slice(bottom + (i + 0.5) * LAYER_HEIGHT) for i in range(layer_count - 1)
    ]
    sections.append(solid.slice(top - last_thickness / 2))

    volume = 0.0
    for i, section in enumerate(sections):
        area = section.area()
        if area == 0:
            continue
        if BOTTOM_LAYERS <= i < layer_count - TOP_LAYERS:
            core = section.offset(-WALL_THICKNESS, JoinType.Round)
            for j in range(i - BOTTOM_LAYERS, i + TOP_LAYERS + 1):
                if core.is_empty():
                    break
                if i != j:
                    core ^= sections[j]
            area -= (1 - INFILL) * core.area()
        thickness = last_thickness if i == layer_count - 1 else LAYER_HEIGHT
        volume += area * thickness
    return volume


def print_material_estimate(solid: Manifold) -> dict[str, float]:
    volume = print_material_volume(solid)
    return {
        "pla_g": volume * PLA_DENSITY / 1000,
        "petg_g": volume * PETG_DENSITY / 1000,
    }
