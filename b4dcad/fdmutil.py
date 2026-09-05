"""Geometry helpers for preparing solids for FDM printing."""

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from manifold3d import CrossSection, Error, Manifold, OpType


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
    points: np.ndarray
    boundary: tuple[int, ...]
    connected: tuple[tuple[int, int], ...]
    bottom: float
    tolerance: float


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
    mesh = manifold.to_mesh64()
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


def _ordered_boundary(boundary):
    adjacency = {}
    for edge, _ in boundary:
        start, end = edge
        adjacency.setdefault(start, []).append(end)
        adjacency.setdefault(end, []).append(start)
    if not adjacency or any(len(neighbors) != 2 for neighbors in adjacency.values()):
        return None

    first = min(adjacency)
    loop = [first]
    previous = None
    current = first
    while True:
        following = next(
            (neighbor for neighbor in adjacency[current] if neighbor != previous), None
        )
        if following is None:
            return None
        if following == first:
            break
        if following in loop:
            return None
        loop.append(following)
        previous, current = current, following
    return loop if len(loop) == len(adjacency) else None


def _horizontal_regions(manifold):
    x_min, y_min, z_min, x_max, y_max, z_max = manifold.bounding_box()
    scale = max(x_max - x_min, y_max - y_min, z_max - z_min, 1.0)
    tolerance = scale * 1e-6
    mesh = manifold.to_mesh64()
    vertices = np.asarray(mesh.vert_properties[:, :3], dtype=np.float64)
    triangles = np.asarray(mesh.tri_verts, dtype=np.int64)
    triangle_points = vertices[triangles]
    area_vectors = np.cross(
        triangle_points[:, 1] - triangle_points[:, 0],
        triangle_points[:, 2] - triangle_points[:, 0],
    )
    lengths = np.linalg.norm(area_vectors, axis=1)
    normals_z = np.zeros(len(triangles))
    valid = lengths > np.finfo(np.float64).eps
    normals_z[valid] = area_vectors[valid, 2] / lengths[valid]
    horizontal = (
        valid
        & (normals_z < -1 + 1e-7)
        & (np.ptp(triangle_points[:, :, 2], axis=1) <= tolerance)
        & (triangle_points[:, 0, 2] > z_min + tolerance)
    )

    edges = _edge_map(triangles)
    regions = []
    for component in _horizontal_components(triangles, horizontal, edges):
        boundary = _boundary_edges(component, triangles, edges)
        loop = _ordered_boundary(boundary)
        if loop is None:
            continue

        component_vertices = sorted(loop)
        local_index = {vertex: index for index, vertex in enumerate(component_vertices)}
        z = float(np.mean(vertices[component_vertices, 2]))
        connected = []
        for edge, neighbors in boundary:
            edge_length = np.linalg.norm(vertices[edge[1], :2] - vertices[edge[0], :2])
            if edge_length <= tolerance:
                continue
            if any(
                np.min(triangle_points[neighbor, :, 2]) < z - tolerance
                for neighbor in neighbors
            ):
                connected.append((local_index[edge[0]], local_index[edge[1]]))
        if not connected or len(connected) == len(boundary):
            continue

        regions.append(
            _HorizontalRegion(
                points=vertices[component_vertices, :2],
                boundary=tuple(local_index[vertex] for vertex in loop),
                connected=tuple(connected),
                bottom=z,
                tolerance=tolerance,
            )
        )
    return regions


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


def _direction_filter(directions):
    if directions == "auto":
        return directions
    if isinstance(directions, str):
        directions = directions.split()
    directions = set(directions)
    allowed = {"<X", ">X", "<Y", ">Y"}
    if not directions or not directions <= allowed:
        raise ValueError("directions must be 'auto' or contain <X, >X, <Y, or >Y")
    return directions


def _distance_to_edge(points, edge):
    start, end = points[list(edge)]
    vector = end - start
    position = np.clip(((points - start) @ vector) / (vector @ vector), 0.0, 1.0)
    nearest = start + position[:, np.newaxis] * vector
    return np.linalg.norm(points - nearest, axis=1)


def _edge_support(region, edge, angle, height):
    """A segment swept by an expanding, inscribed 32-sided disk.

    Each lateral face meets the requested overhang angle or is steeper.
    Align disk vertices with the edge normal so the straight wedge is exact.
    Unlike vertex-sampled distance ramps this does not depend on triangulation.
    """
    start, end = region.points[list(edge)]
    direction = end - start
    phase = np.arctan2(direction[1], direction[0])
    angles = phase + np.arange(32) * (2 * np.pi / 32)
    radius = height * np.tan(np.deg2rad(angle))
    disk = radius * np.column_stack((np.cos(angles), np.sin(angles)))
    upper = np.concatenate((start + disk, end + disk))
    points = np.concatenate(
        (
            np.column_stack(([start, end], [0.0, 0.0])),
            np.column_stack((upper, np.full(len(upper), height))),
        )
    )
    return Manifold.hull_points(points)


def _region_prism(region, height, padding=0.0):
    polygon = region.points[list(region.boundary)]
    signed_area = np.sum(
        polygon[:, 0] * np.roll(polygon[:, 1], -1)
        - polygon[:, 1] * np.roll(polygon[:, 0], -1)
    )
    if signed_area < 0:
        polygon = polygon[::-1]
    section = CrossSection([polygon])
    if padding:
        section = section.offset(padding)
    return section.extrude(height)


def _region_ramp(region, angle, directions, *, pad_boundary=False):
    bounds = (
        np.min(region.points[:, 0]),
        np.min(region.points[:, 1]),
        np.max(region.points[:, 0]),
        np.max(region.points[:, 1]),
    )
    selected = []
    for edge in region.connected:
        if directions != "auto":
            side = _side_for_edge(region.points[list(edge)], bounds, region.tolerance)
            if side not in directions:
                continue
        selected.append(edge)
    if not selected:
        return None

    # This bound covers the entire polygon, including interior distance maxima
    # between multiple anchors. The disk is inscribed, hence the cosine factor.
    height = min(
        np.max(_distance_to_edge(region.points, edge)) for edge in selected
    ) / (np.tan(np.deg2rad(angle)) * np.cos(np.pi / 32))
    height += region.tolerance
    support = Manifold.batch_boolean(
        [_edge_support(region, edge, angle, height) for edge in selected],
        OpType.Add,
    )
    # Extend the cutter across coincident boundaries within the region tolerance.
    # CrossSection quantization otherwise leaves disconnected slivers on curves.
    padding = region.tolerance if pad_boundary else 0.0
    envelope = _region_prism(region, height + padding, padding=padding).translate(
        (0, 0, -padding)
    )
    ramp = envelope - support
    if ramp.is_empty() or ramp.status() != Error.NoError:
        return None
    return ramp


def fix_horizontal_overhangs(
    manifold: Manifold,
    angle: float = 45.0,
    mode: str = "cut",
    directions="auto",
):
    """Cut or add distance-field ramps on simple horizontal faces."""
    if not np.isfinite(angle) or not 0 < angle <= 90:
        raise ValueError("angle must be greater than zero and at most 90 degrees")
    if mode not in ("cut", "add"):
        raise ValueError("mode must be 'cut' or 'add'")
    directions = _direction_filter(directions)
    if manifold.is_empty() or angle == 90:
        return manifold

    modifiers = []
    for region in _horizontal_regions(manifold):
        ramp = _region_ramp(region, angle, directions, pad_boundary=mode == "cut")
        if ramp is None:
            continue

        if mode == "cut":
            modifier = ramp.translate((0, 0, region.bottom))
        else:
            height = ramp.bounding_box()[5]
            if height <= region.tolerance:
                continue
            # Overlap the roof so roundoff cannot leave a coincident inner face.
            prism = _region_prism(region, height + region.tolerance)
            modifier = (prism - ramp).translate((0, 0, region.bottom - height))

        if modifier.is_empty() or modifier.status() != Error.NoError:
            continue
        modifiers.append(modifier)

    if not modifiers:
        return manifold
    modifier = Manifold.batch_boolean(modifiers, OpType.Add)
    return manifold - modifier if mode == "cut" else manifold + modifier


def trim_overhangs(manifold: Manifold, angle: float = 45.0, layer_height=None):
    """Trim simple horizontal overhangs with distance-field ramps.

    ``layer_height`` is accepted for compatibility and is intentionally ignored.
    """
    return fix_horizontal_overhangs(manifold, angle, mode="cut")
