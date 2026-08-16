"""Geometry helpers for preparing solids for FDM printing."""

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from manifold3d import CrossSection, Manifold, OpType


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


@dataclass(frozen=True)
class _HorizontalRegion:
    bounds: tuple[float, float, float, float]
    bottom: float
    top: float
    anchors: tuple[str, ...]


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


def _edge_map(triangles):
    edges = {}
    for triangle_index, triangle in enumerate(triangles):
        for start, end in zip(triangle, np.roll(triangle, -1)):
            edge = tuple(sorted((int(start), int(end))))
            edges.setdefault(edge, []).append(triangle_index)
    return edges


def _horizontal_components(triangles, horizontal, edges):
    remaining = set(np.flatnonzero(horizontal))
    components = []
    while remaining:
        first = remaining.pop()
        component = {first}
        pending = [first]
        while pending:
            triangle_index = pending.pop()
            for start, end in zip(
                triangles[triangle_index], np.roll(triangles[triangle_index], -1)
            ):
                edge = tuple(sorted((int(start), int(end))))
                for neighbor in edges[edge]:
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        component.add(neighbor)
                        pending.append(neighbor)
        components.append(component)
    return components


def _boundary_edges(component, triangles, edges):
    boundary = []
    for triangle_index in component:
        triangle = triangles[triangle_index]
        for start, end in zip(triangle, np.roll(triangle, -1)):
            edge = tuple(sorted((int(start), int(end))))
            neighbors = [index for index in edges[edge] if index not in component]
            if neighbors:
                boundary.append((edge, neighbors))
    return boundary


def _side_for_edge(points, bounds, tolerance):
    x_min, y_min, x_max, y_max = bounds
    if np.all(np.abs(points[:, 0] - x_min) <= tolerance):
        return "<X"
    if np.all(np.abs(points[:, 0] - x_max) <= tolerance):
        return ">X"
    if np.all(np.abs(points[:, 1] - y_min) <= tolerance):
        return "<Y"
    if np.all(np.abs(points[:, 1] - y_max) <= tolerance):
        return ">Y"
    return None


def _component_top(manifold, vertices, triangles, component, z, tolerance, z_max):
    top_heights = []
    endpoint = z_max + max(1.0, z_max - z)
    for triangle_index in component:
        point = np.mean(vertices[triangles[triangle_index]], axis=0)
        origin = (point[0], point[1], z + tolerance)
        hits = manifold.ray_cast(origin, (point[0], point[1], endpoint))
        heights = [hit.position[2] for hit in hits if hit.position[2] > z + tolerance]
        if not heights:
            return None
        top_heights.append(min(heights))

    if max(top_heights) - min(top_heights) > tolerance * 10:
        return None
    return float(np.mean(top_heights))


def _wedge_polygon(anchor, free, z_bottom, z_top, tangent):
    run = abs(free - anchor)
    thickness = z_top - z_bottom
    cap_distance = thickness * tangent
    direction = np.sign(free - anchor)

    if cap_distance >= run:
        polygon = np.array(
            [
                (anchor, z_bottom),
                (free, z_bottom),
                (free, z_bottom + run / tangent),
            ]
        )
    else:
        polygon = np.array(
            [
                (anchor, z_bottom),
                (free, z_bottom),
                (free, z_top),
                (anchor + direction * cap_distance, z_top),
            ]
        )

    signed_area = np.sum(
        polygon[:, 0] * np.roll(polygon[:, 1], -1)
        - polygon[:, 1] * np.roll(polygon[:, 0], -1)
    )
    return polygon if signed_area > 0 else polygon[::-1]


def _support_polygon(anchor, free, z, tangent):
    depth = abs(free - anchor) / tangent
    polygon = np.array([(anchor, z), (free, z), (anchor, z - depth)])
    signed_area = np.sum(
        polygon[:, 0] * np.roll(polygon[:, 1], -1)
        - polygon[:, 1] * np.roll(polygon[:, 0], -1)
    )
    return polygon if signed_area > 0 else polygon[::-1]


def _extrude_axis_profile(polygon, bounds, axis):
    x_min, y_min, x_max, y_max = bounds
    if axis == "X":
        local = CrossSection([polygon]).extrude(y_max - y_min)
        return local.transform(((1, 0, 0, 0), (0, 0, 1, y_min), (0, 1, 0, 0)))

    local = CrossSection([polygon]).extrude(x_max - x_min)
    return local.transform(((0, 0, 1, x_min), (1, 0, 0, 0), (0, 1, 0, 0)))


def _wedge(bounds, z_bottom, z_top, anchor, angle):
    x_min, y_min, x_max, y_max = bounds
    tangent = np.tan(np.deg2rad(angle))
    if tangent <= np.finfo(np.float64).eps:
        tangent = np.finfo(np.float64).eps

    if anchor in ("<X", ">X"):
        anchor_x, free_x = (x_min, x_max) if anchor == "<X" else (x_max, x_min)
        polygon = _wedge_polygon(anchor_x, free_x, z_bottom, z_top, tangent)
        return _extrude_axis_profile(polygon, bounds, "X")

    anchor_y, free_y = (y_min, y_max) if anchor == "<Y" else (y_max, y_min)
    polygon = _wedge_polygon(anchor_y, free_y, z_bottom, z_top, tangent)
    return _extrude_axis_profile(polygon, bounds, "Y")


def _component_cutter(bounds, z_bottom, z_top, anchors, angle):
    wedges = [_wedge(bounds, z_bottom, z_top, anchor, angle) for anchor in anchors]
    if len(wedges) == 1:
        return wedges[0]
    return Manifold.batch_boolean(wedges, OpType.Intersect)


def _support_wedge(bounds, z, anchor, angle):
    x_min, y_min, x_max, y_max = bounds
    tangent = np.tan(np.deg2rad(angle))
    if anchor in ("<X", ">X"):
        anchor_x, free_x = (x_min, x_max) if anchor == "<X" else (x_max, x_min)
        polygon = _support_polygon(anchor_x, free_x, z, tangent)
        return _extrude_axis_profile(polygon, bounds, "X")

    anchor_y, free_y = (y_min, y_max) if anchor == "<Y" else (y_max, y_min)
    polygon = _support_polygon(anchor_y, free_y, z, tangent)
    return _extrude_axis_profile(polygon, bounds, "Y")


def _component_support(bounds, z, anchors, angle):
    x_min, y_min, x_max, y_max = bounds
    if len(anchors) == 1:
        return _support_wedge(bounds, z, anchors[0], angle)

    supports = []
    if anchors[0][1] == "X":
        middle = (x_min + x_max) / 2
        for anchor in anchors:
            half_bounds = (
                (x_min, y_min, middle, y_max)
                if anchor == "<X"
                else (middle, y_min, x_max, y_max)
            )
            supports.append(_support_wedge(half_bounds, z, anchor, angle))
    else:
        middle = (y_min + y_max) / 2
        for anchor in anchors:
            half_bounds = (
                (x_min, y_min, x_max, middle)
                if anchor == "<Y"
                else (x_min, middle, x_max, y_max)
            )
            supports.append(_support_wedge(half_bounds, z, anchor, angle))
    return Manifold.batch_boolean(supports, OpType.Add)


def _horizontal_regions(manifold):
    x_min, y_min, z_min, x_max, y_max, z_max = manifold.bounding_box()
    scale = max(x_max - x_min, y_max - y_min, z_max - z_min, 1.0)
    tolerance = scale * 1e-6
    mesh = manifold.to_mesh()
    vertices = np.asarray(mesh.vert_properties[:, :3], dtype=np.float64)
    triangles = np.asarray(mesh.tri_verts, dtype=np.int64)
    points = vertices[triangles]
    area_vectors = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    lengths = np.linalg.norm(area_vectors, axis=1)
    normals_z = np.zeros(len(triangles))
    valid = lengths > np.finfo(np.float64).eps
    normals_z[valid] = area_vectors[valid, 2] / lengths[valid]
    horizontal = (
        valid
        & (normals_z < -1 + 1e-7)
        & (np.ptp(points[:, :, 2], axis=1) <= tolerance)
        & (points[:, 0, 2] > z_min + tolerance)
    )

    edges = _edge_map(triangles)
    regions = []
    for component in _horizontal_components(triangles, horizontal, edges):
        indices = np.fromiter(component, dtype=np.int64)
        component_points = points[indices]
        z = float(np.mean(component_points[:, :, 2]))
        bounds = (
            float(np.min(component_points[:, :, 0])),
            float(np.min(component_points[:, :, 1])),
            float(np.max(component_points[:, :, 0])),
            float(np.max(component_points[:, :, 1])),
        )
        width = bounds[2] - bounds[0]
        depth = bounds[3] - bounds[1]
        area = float(np.sum(lengths[indices]) / 2)
        if width <= tolerance or depth <= tolerance:
            continue
        if abs(area - width * depth) > max(area, width * depth) * 1e-5:
            continue

        anchors = set()
        valid_boundary = True
        for edge, neighbors in _boundary_edges(component, triangles, edges):
            side = _side_for_edge(vertices[list(edge), :2], bounds, tolerance)
            if side is None:
                valid_boundary = False
                break
            if any(
                np.min(points[neighbor, :, 2]) < z - tolerance for neighbor in neighbors
            ):
                anchors.add(side)
        if not valid_boundary:
            continue

        x_anchors = anchors & {"<X", ">X"}
        y_anchors = anchors & {"<Y", ">Y"}
        if x_anchors and not y_anchors:
            selected_anchors = tuple(sorted(x_anchors))
        elif y_anchors and not x_anchors:
            selected_anchors = tuple(sorted(y_anchors))
        else:
            continue

        top = _component_top(
            manifold, vertices, triangles, component, z, tolerance, z_max
        )
        if top is None or top - z <= tolerance:
            continue
        regions.append(_HorizontalRegion(bounds, z, top, selected_anchors))
    return regions


def _direction_filter(directions):
    if directions == "auto":
        return None
    if isinstance(directions, str):
        directions = directions.split()
    directions = set(directions)
    allowed = {"<X", ">X", "<Y", ">Y"}
    if not directions or not directions <= allowed:
        raise ValueError("directions must be 'auto' or contain <X, >X, <Y, or >Y")
    return directions


def fix_horizontal_overhangs(
    manifold: Manifold,
    angle: float = 45.0,
    mode: str = "cut",
    directions="auto",
):
    """Cut or add wedges on simple axis-aligned horizontal overhangs."""
    if not np.isfinite(angle) or not 0 <= angle <= 90:
        raise ValueError("angle must be between 0 and 90 degrees")
    if mode not in ("cut", "add"):
        raise ValueError("mode must be 'cut' or 'add'")
    if mode == "add" and angle == 0:
        raise ValueError("angle must be greater than zero in add mode")
    direction_filter = _direction_filter(directions)
    if manifold.is_empty() or angle == 90:
        return manifold

    modifiers = []
    for region in _horizontal_regions(manifold):
        anchors = region.anchors
        if direction_filter is not None:
            anchors = tuple(anchor for anchor in anchors if anchor in direction_filter)
        if not anchors:
            continue
        if mode == "cut":
            modifiers.append(
                _component_cutter(
                    region.bounds, region.bottom, region.top, anchors, angle
                )
            )
        else:
            modifiers.append(
                _component_support(region.bounds, region.bottom, anchors, angle)
            )

    if not modifiers:
        return manifold
    modifier = Manifold.batch_boolean(modifiers, OpType.Add)
    return manifold - modifier if mode == "cut" else manifold + modifier


def trim_overhangs(manifold: Manifold, angle: float = 45.0, layer_height=None):
    """Trim simple horizontal overhangs with axis-aligned sloped wedges.

    ``layer_height`` is accepted for compatibility and is intentionally ignored.
    """
    return fix_horizontal_overhangs(manifold, angle, mode="cut")
