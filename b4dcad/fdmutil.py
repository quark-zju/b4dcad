"""Geometry helpers for preparing solids for FDM printing."""

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from manifold3d import Manifold, OpType


@dataclass(frozen=True)
class OverhangResult:
    """Downward-facing triangles steeper than an allowed overhang angle."""

    triangle_indices: np.ndarray
    normals: np.ndarray
    angles: np.ndarray
    areas: np.ndarray

    @property
    def area(self):
        """Total surface area of the detected triangles."""
        return float(np.sum(self.areas))

    def is_empty(self):
        return self.triangle_indices.size == 0


def _unit_vector(vector: Sequence[float]):
    vector = np.asarray(vector, dtype=np.float64)
    if vector.shape != (3,):
        raise ValueError("build_direction must contain exactly three values")
    length = np.linalg.norm(vector)
    if not np.isfinite(length) or length == 0:
        raise ValueError("build_direction must be a finite, non-zero vector")
    return vector / length


def detect_overhangs(
    manifold: Manifold,
    angle: float = 45.0,
    build_direction: Sequence[float] = (0.0, 0.0, 1.0),
):
    """Detect locally overhanging triangles using their face normals.

    ``angle`` is measured from vertical: a vertical wall is zero degrees and
    a horizontal underside is 90 degrees. The test is local and deliberately
    does not try to recognize bridges or support from geometry below.
    """
    if not np.isfinite(angle) or not 0 <= angle <= 90:
        raise ValueError("angle must be between 0 and 90 degrees")

    direction = _unit_vector(build_direction)
    mesh = manifold.to_mesh()
    vertices = np.asarray(mesh.vert_properties[:, :3], dtype=np.float64)
    triangles = np.asarray(mesh.tri_verts, dtype=np.int64)

    sides_a = vertices[triangles[:, 1]] - vertices[triangles[:, 0]]
    sides_b = vertices[triangles[:, 2]] - vertices[triangles[:, 0]]
    area_vectors = np.cross(sides_a, sides_b)
    lengths = np.linalg.norm(area_vectors, axis=1)

    normals = np.zeros_like(area_vectors)
    valid = lengths > np.finfo(np.float64).eps
    normals[valid] = area_vectors[valid] / lengths[valid, np.newaxis]

    downward = -(normals @ direction)
    angles = np.degrees(np.arcsin(np.clip(downward, 0.0, 1.0)))
    selected = valid & (downward > 0) & (angles > angle + 1e-7)

    return OverhangResult(
        triangle_indices=np.flatnonzero(selected),
        normals=normals[selected],
        angles=angles[selected],
        areas=lengths[selected] / 2,
    )


def trim_overhangs(
    manifold: Manifold,
    angle: float = 45.0,
    layer_height: float = 0.2,
):
    """Remove material that cannot be reached from the layer below.

    The build direction is positive Z. ``angle`` is measured from vertical,
    and each layer may grow horizontally by ``layer_height * tan(angle)``.
    This produces a layer-wise approximation of the requested slope. It does
    not attempt special handling for bridges or disconnected floating parts.
    """
    if not np.isfinite(angle) or not 0 <= angle <= 90:
        raise ValueError("angle must be between 0 and 90 degrees")
    if not np.isfinite(layer_height) or layer_height <= 0:
        raise ValueError("layer_height must be finite and greater than zero")
    if manifold.is_empty() or angle == 90:
        return manifold

    _, _, z_min, _, _, z_max = manifold.bounding_box()
    height = z_max - z_min
    if height <= np.finfo(np.float64).eps:
        return manifold

    layer_count = int(np.ceil(height / layer_height))
    if layer_count > 10_000:
        raise ValueError("layer_height would require more than 10000 layers")

    step = height / layer_count
    growth = step * np.tan(np.deg2rad(angle))
    previous = None
    layers = []

    for layer in range(layer_count):
        z_bottom = z_min + layer * step
        section = manifold.slice(z_bottom + step / 2)
        if previous is None:
            printable = section
        elif previous.is_empty():
            printable = previous
        else:
            printable = section ^ previous.offset(growth)

        if not printable.is_empty():
            layers.append(printable.extrude(step).translate((0, 0, z_bottom)))
        previous = printable

    if not layers:
        return Manifold()

    stepped_envelope = Manifold.batch_boolean(layers, OpType.Add)
    return stepped_envelope ^ manifold
