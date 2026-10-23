from typing import NamedTuple

import io
import zipfile
from xml.sax.saxutils import quoteattr

import manifold3d
import numpy as np
from manifold3d import CrossSection, Manifold, Mesh

from .fdmutil import detect_overhangs as _detect_overhangs
from .fdmutil import fix_horizontal_overhangs as _fix_horizontal_overhangs
from .fdmutil import trim_overhangs as _trim_overhangs
from .loft import polygon_nearest_alignment, ring as _loft_ring, stitch as _loft_stitch
from .normals import triangle_normals
from .svg import svg2polygons
from .text import text2svg
from .dxf import dxf2polygons

# Majority vote avoids most edge/vertex ambiguities in ray intersections.
_CONTAINS_RAYS = [
    np.array(d, dtype=float) / np.linalg.norm(d)
    for d in [
        (1, 0, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 1, 1),
        (-1, 1, 1),
        (1, -1, 1),
        (1, 1, -1),
    ]
]

stl_dtype = np.dtype(
    [("norm", np.float32, 3), ("vert", np.float32, 9), ("pad", np.int8, 2)]
)
CQ_TESSELLATION_TOLERANCE = 0.05


class Size(NamedTuple):
    x: float
    y: float
    z: float


def _split_faces(faces):
    return faces.split() if isinstance(faces, str) else list(faces)


def _axis_index(axis, axes):
    axis = axis.upper()
    if axis not in axes:
        raise ValueError(f"unsupported axis {axis!r}; expected one of {axes}")
    return axes.index(axis)


def _face_anchor(bounds, face, axes):
    if face.startswith("-"):
        i = _axis_index(face[1:], axes)
        return i, (bounds[i] + bounds[i + len(axes)]) / 2

    if face.startswith("<"):
        i = _axis_index(face[1:], axes)
        return i, bounds[i]

    if face.startswith(">"):
        i = _axis_index(face[1:], axes)
        return i, bounds[i + len(axes)]

    raise ValueError(f"unsupported face selector {face!r}")


def _opposite_face(face):
    if face.startswith("<"):
        return ">" + face[1:]
    if face.startswith(">"):
        return "<" + face[1:]
    return face


def _align_delta(source_bounds, target_bounds, face, axes):
    # Face selectors follow the homecad/cqutils convention:
    #   >X / <X: align the same max/min side on that axis.
    #   :>X: put source <X against target >X instead of overlapping faces.
    #   -X: align centers on that axis.
    if face.startswith(":"):
        target_face = face[1:]
        source_face = _opposite_face(target_face)
    else:
        target_face = source_face = face

    source_axis, source = _face_anchor(source_bounds, source_face, axes)
    target_axis, target = _face_anchor(target_bounds, target_face, axes)
    if source_axis != target_axis:
        raise ValueError(f"face selector {face!r} resolves to mismatched axes")

    delta = [0] * len(axes)
    delta[source_axis] = target - source
    return delta


def _unit_vector(value, name):
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must be a finite 3D vector")
    length = np.linalg.norm(vector)
    if length == 0:
        raise ValueError(f"{name} must be nonzero")
    return vector / length


# wrapper for Manifold
# tweaks API for scriptable modeling
class Solid:
    def __init__(self, manifold=Manifold()):
        self.manifold = manifold

    # TODO add visual properties (e.g. color, texture)

    def __add__(self, other):
        return Solid(self.manifold + other.manifold)

    def __sub__(self, other):
        return Solid(self.manifold - other.manifold)

    def __and__(self, other):
        # manifold3d XOR is actually AND
        return Solid(self.manifold ^ other.manifold)

    def as_original(self):
        return Solid(self.manifold.as_original())

    def bounding_box(self):
        return self.manifold.bounding_box()

    def size(self):
        x0, y0, z0, x1, y1, z1 = self.bounding_box()
        return Size(x1 - x0, y1 - y0, z1 - z0)

    def calculate_curvature(self, gaussian_idx: int, mean_idx: int):
        return Solid(self.manifold.calculate_curvature(gaussian_idx, mean_idx))

    def align(
        self,
        xmin=None,
        x=None,
        xmax=None,
        ymin=None,
        y=None,
        ymax=None,
        zmin=None,
        z=None,
        zmax=None,
    ):
        x0, y0, z0, x1, y1, z1 = self.bounding_box()
        dx, dy, dz = 0.0, 0.0, 0.0
        if xmin is not None:
            dx = xmin - x0
        if x is not None:
            dx = x - (x0 + x1) / 2
        if xmax is not None:
            dx = xmax - x1
        if ymin is not None:
            dy = ymin - y0
        if y is not None:
            dy = y - (y0 + y1) / 2
        if ymax is not None:
            dy = ymax - y1
        if zmin is not None:
            dz = zmin - z0
        if z is not None:
            dz = z - (z0 + z1) / 2
        if zmax is not None:
            dz = zmax - z1
        return self.move(dx, dy, dz)

    def align_to(self, other, faces="", dx=0.0, dy=0.0, dz=0.0):
        """Align this solid to another solid using bounding-box face selectors.

        Args:
            other: Target ``Solid`` to align to.
            faces: Face selector string/list, such as ``">X <Y"``, ``":>X"``,
                or ``"-Z"``. Multiple selectors are applied in order.
            dx: Extra X translation after face alignment.
            dy: Extra Y translation after face alignment.
            dz: Extra Z translation after face alignment.

        Selector semantics:
            ``>X`` / ``<X`` align the same max/min faces on that axis.
            ``:>X`` aligns this solid's opposite face against the target face,
            so this solid's ``<X`` touches the target's ``>X`` instead of
            overlapping it. ``-X``, ``-Y``, and ``-Z`` align centers.
        """
        solid = self
        for face in _split_faces(faces):
            cdx, cdy, cdz = _align_delta(
                solid.bounding_box(),
                other.bounding_box(),
                face,
                ("X", "Y", "Z"),
            )
            solid = solid.move(cdx, cdy, cdz)
        return solid.move(dx, dy, dz)

    def decompose(self):
        return [Solid(m) for m in self.manifold.decompose()]

    def detect_overhangs(self, angle=45.0, build_direction=(0.0, 0.0, 1.0)):
        """Return locally overhanging faces for the requested FDM angle."""
        return _detect_overhangs(self.manifold, angle, build_direction)

    def genus(self):
        return self.manifold.genus()

    def fix_horizontal_overhangs(self, angle=45.0, mode="cut", directions="auto"):
        """Cut or add wedges on simple horizontal overhangs."""
        return Solid(
            _fix_horizontal_overhangs(
                self.manifold,
                angle=angle,
                mode=mode,
                directions=directions,
            )
        )

    def get_surface_area(self):
        return self.manifold.surface_area()

    def get_volume(self):
        return self.manifold.volume()

    def hull(self, *others):
        return Solid(
            Manifold.batch_hull([self.manifold, *[o.manifold for o in others]])
        )

    def is_empty(self):
        return self.manifold.is_empty()

    def mirror(self, x=0.0, y=0.0, z=0.0):
        return Solid(self.manifold.mirror((x, y, z)))

    def num_edge(self):
        return self.manifold.num_edge()

    def num_prop(self):
        return self.manifold.num_prop()

    def num_prop_vert(self):
        return self.manifold.num_prop_vert()

    def num_tri(self):
        return self.manifold.num_tri()

    def num_vert(self):
        return self.manifold.num_vert()

    def original_id(self):
        return self.manifold.original_id()

    def precision(self):
        return self.manifold.get_tolerance()

    def refine(self, n=2):
        return Solid(self.manifold.refine(n))

    def rotate(self, x=0, y=0, z=0, origin=None):
        """Rotate by x, then y, then z degrees about the world axes. With
        `origin`, rotate about that point and not about (0, 0, 0)."""
        if origin is None:
            return Solid(self.manifold.rotate((x, y, z)))
        ox, oy, oz = origin
        return self.move(-ox, -oy, -oz).rotate(x, y, z).move(ox, oy, oz)

    def rotate_axis(self, axis, degree):
        axis = axis.upper()
        _axis_index(axis, ("X", "Y", "Z"))
        kwargs = {"x": 0.0, "y": 0.0, "z": 0.0}
        kwargs[axis.lower()] = degree
        return self.rotate(**kwargs)

    def scale(self, x=1.0, y=1.0, z=1.0):
        return Solid(self.manifold.scale((x, y, z)))

    def set_properties(self, *args, **kwargs):
        raise ValueError("not implemented")

    def split(self, cutter):
        inter, diff = self.manifold.split(
            cutter.manifold if isinstance(cutter, Solid) else cutter
        )
        return Solid(inter), Solid(diff)

    def split_by_plane(self, x=0.0, y=0.0, z=0.0, offset=0.0):
        top, bottom = self.manifold.split_by_plane((x, y, z), offset)
        return Solid(top), Solid(bottom)

    def status(self):
        return self.manifold.status()

    def to_mesh(self, normal_idx=-1):
        return self.manifold.to_mesh(normal_idx)

    def transform(self, matrix):
        return Solid(self.manifold.transform(matrix))

    def move(self, x=0.0, y=0.0, z=0.0):
        return Solid(self.manifold.translate((x, y, z)))

    def trim_by_plane(self, x=0.0, y=0.0, z=0.0, offset=0.0):
        return Solid(self.manifold.trim_by_plane((x, y, z), offset))

    def trim_overhangs(self, angle=45.0, layer_height=None):
        """Remove simple horizontal overhangs; layer_height is ignored."""
        return Solid(_trim_overhangs(self.manifold, angle, layer_height))

    def warp(self, xyz_map_fn):
        return Solid(self.manifold.warp(xyz_map_fn))

    def warp_batch(self, xyz_map_fn):
        return Solid(self.manifold.warp_batch(xyz_map_fn))

    def refine_to_length(self, edge_len):
        m = self.manifold.to_mesh()
        verts = m.vert_properties.tolist()
        tris = m.tri_verts.tolist()
        mids = {}
        i = 0
        while i < len(tris):
            tri = tris[i]
            v = [verts[i] for i in tri]
            dv = v - np.roll(v, 1, 0)
            lens = np.linalg.norm(dv, axis=-1)
            mi = np.argmax(lens)
            if lens[mi] > edge_len:
                key = (min(tri[mi], tri[mi - 1]), max(tri[mi], tri[mi - 1]))
                if key not in mids:
                    mididx = len(verts)
                    midv = [(v[mi][j] + v[mi - 1][j]) / 2 for j in [0, 1, 2]]
                    verts += [midv]
                    mids[key] = mididx
                else:
                    mididx = mids[key]
                tri2 = [*tri]
                tri2[mi - 1] = mididx
                tris += [tri2]
                tri[mi] = mididx
            else:
                i += 1

        verts = np.array(verts, np.float32)
        tris = np.array(tris, np.int32)
        m = manifold3d.Mesh(verts, tris, face_id=np.arange(len(tris)))
        return Solid(Manifold(m))

    def stl(self, filename=None):
        mesh = self.to_mesh()
        tris = mesh.tri_verts
        verts = mesh.vert_properties

        header = np.zeros(21, dtype=np.uint32)
        header[20] = tris.shape[0]
        body = np.zeros(tris.shape[0], stl_dtype)
        body["norm"] = triangle_normals(verts, tris)
        body["vert"] = verts[tris].reshape(-1, 9)
        binary = header.tobytes() + body.tobytes()
        if filename:
            with open(filename, "wb") as f:
                f.write(binary)
            return self
        else:
            return binary

    def to_cq(self):
        """Convert this b4dcad Solid to a CadQuery Workplane.

        This is a slow fallback path: Manifold geometry is serialized as STL,
        sewn into OCCT shells, converted to solids, and simplified. Prefer doing
        topology-sensitive OCCT operations such as fillet/chamfer in CadQuery
        first, then call from_cq() once before doing dense holes/repeated
        booleans in b4dcad.

        CadQuery and OCP are imported lazily here so normal b4dcad scripts do
        not pay their import cost unless this bridge is actually used.
        """
        return _solid_to_cq(self)

    def orient(self, direction, origin=(0, 0, 0), x_axis=None):
        """Turn and move so that local +z points along `direction` and the
        local origin lands on `origin`.

        Build a part along +z (a cylinder, a hole, threads), then orient it:
            cylinder(h=10, d=3).orient((1, 1, 0), origin=(5, 0, 2))
        `x_axis` sets the roll about the axis: local +x points as close to
        it as it can (default: world z, or world x if direction is near z)."""
        z = _unit_vector(direction, "direction")
        if x_axis is None:
            x_axis = (1, 0, 0) if abs(z[2]) > 0.9 else (0, 0, 1)
        x = np.array(x_axis, dtype=float)
        x = x - (x @ z) * z
        if np.linalg.norm(x) < 1e-9:
            raise ValueError("x_axis is parallel to direction")
        x /= np.linalg.norm(x)
        y = np.cross(z, x)
        m = np.column_stack([x, y, z, np.array(origin, dtype=float)])
        return Solid(self.manifold.transform(m))

    def section(self, normal=(0, 0, 1), origin=(0, 0, 0), x_axis=None):
        """Cut with the plane through `origin` with `normal`; return a Shape.

        2D frame: `origin` maps to (0, 0), the 2D x axis is `x_axis` (default:
        the world x axis projected into the plane, or world y if normal is
        close to x), and the 2D y axis is normal x x_axis (right handed).
        With the default arguments this is the same as cross_section(s, 0)."""
        n = _unit_vector(normal, "normal")
        if x_axis is None:
            x_axis = (1, 0, 0) if abs(n[0]) < 0.9 else (0, 1, 0)
        u = np.array(x_axis, dtype=float)
        u = u - (u @ n) * n
        u = _unit_vector(u, "projected x_axis")
        v = np.cross(n, u)
        o = np.array(origin, dtype=float)
        rot = np.stack([u, v, n])
        m = np.column_stack([rot, -rot @ o])
        return Shape(self.manifold.transform(m).slice(0))

    def prune(self, min_volume=1e-3, largest_only=False):
        """Drop separate bodies smaller than `min_volume` (mm^3): the thin
        skins and slivers that booleans can leave where faces almost meet.
        With largest_only=True, keep only the biggest body."""
        parts = sorted(
            self.manifold.decompose(), key=lambda m: m.volume(), reverse=True
        )
        if largest_only:
            parts = parts[:1]
        parts = [m for m in parts if m.volume() >= min_volume]
        return Solid(Manifold.compose(parts)) if parts else Solid()

    def contains(self, point):
        """True if `point` is inside the solid.

        Casts rays from the point to beyond the bounding box, along the
        three axes and the four cube diagonals, and counts the surface hits
        of each ray: an odd count is inside. A ray that runs through an edge
        or a vertex can count wrong, so the majority of the seven rays wins."""
        p = np.array(point, dtype=float)
        if p.shape != (3,) or not np.all(np.isfinite(p)):
            raise ValueError("point must be a finite 3D point")
        if self.is_empty():
            return False
        x0, y0, z0, x1, y1, z1 = self.bounding_box()
        if not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1 and z0 <= p[2] <= z1):
            return False
        span = np.linalg.norm([x1 - x0, y1 - y0, z1 - z0]) + 1.0
        inside = 0
        for d in _CONTAINS_RAYS:
            hits = self.manifold.ray_cast(tuple(p), tuple(p + d * span))
            inside += len(hits) % 2
        return bool(inside > len(_CONTAINS_RAYS) // 2)

    def min_gap(self, other, search_length=None):
        """Smallest distance between this solid and `other` (0 if they
        touch or overlap). The default search length covers both
        bounding boxes, so the result is the real gap."""
        if search_length is None:
            a = np.array(self.bounding_box()).reshape(2, 3)
            b = np.array(other.bounding_box()).reshape(2, 3)
            lo, hi = np.minimum(a[0], b[0]), np.maximum(a[1], b[1])
            search_length = float(np.linalg.norm(hi - lo)) + 1.0
        return self.manifold.min_gap(other.manifold, search_length)

    def simplify(self, tolerance=0):
        """Remove vertices that move no surface more than `tolerance` (mm):
        the extra edges that booleans and refine() leave on flat faces.
        With 0, use the tolerance of the solid (see precision())."""
        return Solid(self.manifold.simplify(tolerance))

    def project(self):
        """The outline of the solid seen from +z (its shadow on the xy
        plane), as a Shape."""
        return Shape(self.manifold.project())

    def smooth(self, n=4, min_sharp_angle=52.5, min_smoothness=0):
        """Round the faceted surface: split each edge into n parts and move
        the new vertices onto a smooth (G1) surface through the old ones.
        Edges with a dihedral angle above `min_sharp_angle` (degrees) stay
        sharp. `min_smoothness` (0 to 1) rounds the sharp edges a little."""
        return Solid(
            self.manifold.smooth_out(min_sharp_angle, min_smoothness).refine(n)
        )

    def minkowski_sum(self, other):
        return Solid(self.manifold.minkowski_sum(other.manifold))

    def minkowski_difference(self, other):
        return Solid(self.manifold.minkowski_difference(other.manifold))

    def threemf(self, filename=None, name="part", color=None):
        """Write a 3MF file with this solid as one object. See save_3mf().
        Without a filename, return the file as bytes."""
        out = save_3mf(filename, [(name, self, color)])
        return self if filename else out


class Shape:
    def __init__(self, cross_section=CrossSection()):
        self.cross_section = cross_section

    def __add__(self, other):
        return Shape(self.cross_section + other.cross_section)

    def __sub__(self, other):
        return Shape(self.cross_section - other.cross_section)

    def __and__(self, other):
        # manifold3d XOR is actually AND
        return Shape(self.cross_section ^ other.cross_section)

    def area(self):
        return self.cross_section.area()

    def bounds(self):
        return self.cross_section.bounds()

    def align(self, xmin=None, x=None, xmax=None, ymin=None, y=None, ymax=None):
        x0, y0, x1, y1 = self.bounds()
        dx, dy = 0.0, 0.0
        if xmin is not None:
            dx = xmin - x0
        if x is not None:
            dx = x - (x0 + x1) / 2
        if xmax is not None:
            dx = xmax - x1
        if ymin is not None:
            dy = ymin - y0
        if y is not None:
            dy = y - (y0 + y1) / 2
        if ymax is not None:
            dy = ymax - y1
        return self.move(dx, dy)

    def align_to(self, other, faces="", dx=0.0, dy=0.0):
        """Align this 2D shape to another shape using bounding-box selectors.

        Args:
            other: Target ``Shape`` to align to.
            faces: Face selector string/list, such as ``">X <Y"``, ``":>X"``,
                or ``"-Y"``. Multiple selectors are applied in order.
            dx: Extra X translation after face alignment.
            dy: Extra Y translation after face alignment.

        Selector semantics match ``Solid.align_to`` but only support X/Y axes.
        ``:`` selectors place the shapes next to each other; ``-`` selectors
        align centers.
        """
        shape = self
        for face in _split_faces(faces):
            cdx, cdy = _align_delta(
                shape.bounds(),
                other.bounds(),
                face,
                ("X", "Y"),
            )
            shape = shape.move(cdx, cdy)
        return shape.move(dx, dy)

    def decompose(self):
        return [Shape(p) for p in self.cross_section.decompose()]

    def extrude(self, height, fn=0, twist=0.0, scale_top=(1.0, 1.0), center=False):
        s = Solid(
            self.cross_section.extrude(
                height,
                n_divisions=fn,
                twist_degrees=twist,
                scale_top=scale_top,
            )
        )
        return s.move(z=-height / 2) if center else s

    def extrude_to(self, other, height, center=False):
        polys1 = self.to_polygons()
        assert len(polys1) == 1, "extrude_to only supports simple polygons"
        verts1 = np.pad(polys1[0], [[0, 0], [0, 1]], constant_values=0)
        N1 = verts1.shape[0]

        polys2 = other.to_polygons()
        assert len(polys2) == 1, "extrude_to only supports simple polygons"
        verts2 = np.pad(polys2[0], [[0, 0], [0, 1]], constant_values=height)

        # flip the bottom over
        tris1 = manifold3d.triangulate(polys1)
        tmp = tris1[:, 1].copy()
        tris1[:, 1] = tris1[:, 2]
        tris1[:, 2] = tmp

        # offset top vertex indices
        tris2 = manifold3d.triangulate(polys2)
        tris2 += N1

        alignment = polygon_nearest_alignment(verts1, verts2)
        alignment = [(a, b + N1) for a, b in alignment]

        # build the skirt faces
        tris3 = []
        for s in range(len(alignment)):
            i, j = alignment[s]
            pi, pj = alignment[s - 1]
            if i != pi:
                tris3 += [[pi, i, pj]]
            if j != pj:
                tris3 += [[i, j, pj]]
        tris3 = np.array(tris3)

        verts = np.concatenate((verts1, verts2))
        tris = np.concatenate((tris1, tris2, tris3))
        mesh = manifold3d.Mesh(verts, tris)
        s = Solid(Manifold(mesh))
        return s.move(z=-height / 2) if center else s

    def refine_to_length(self, l):
        def refine_poly(p, l):
            pts = []
            for i in range(0, len(p)):
                d = np.linalg.norm(p[i] - p[i - 1])
                n = max(1, int(np.ceil(d / l)))
                for j in range(1, n):
                    lerp = p[i - 1] * (n - j) / n
                    lerp += p[i] * j / n
                    pts += [lerp]
                pts += [p[i]]
            return np.stack(pts, axis=0)

        polys = [refine_poly(p, l) for p in self.to_polygons()]
        return Shape(CrossSection(polys))

    def hull(self, *others):
        return Shape(
            CrossSection.batch_hull(
                [self.cross_section, *[o.cross_section for o in others]]
            )
        )

    def is_empty(self):
        return self.cross_section.is_empty()

    def mirror(self, x=0.0, y=0.0):
        return Shape(self.cross_section.mirror((x, y)))

    def num_contour(self):
        return self.cross_section.num_contour()

    def num_vert(self):
        return self.cross_section.num_vert()

    def offset(self, delta, join_type="miter", miter_limit=2.0, circular_segments=0):
        if join_type == "round":
            join_type = manifold3d.JoinType.Round
        elif join_type == "miter":
            join_type = manifold3d.JoinType.Miter
        elif join_type == "square":
            join_type = manifold3d.JoinType.Square
        else:
            raise ValueError(f"{join_type=}")
        return Shape(
            self.cross_section.offset(delta, join_type, miter_limit, circular_segments)
        )

    def revolve(self, z=360.0, fn=0):
        return Solid(
            self.cross_section.revolve(
                circular_segments=fn,
                revolve_degrees=z,
            )
        )

    def rotate(self, z, origin=None):
        """Rotate by z degrees. With `origin`, rotate about that point and
        not about (0, 0)."""
        if origin is None:
            return Shape(self.cross_section.rotate(z))
        ox, oy = origin
        return self.move(-ox, -oy).rotate(z).move(ox, oy)

    def rotate_axis(self, axis, degree):
        if axis.upper() != "Z":
            raise ValueError("2D shapes can only rotate around the Z axis")
        return self.rotate(degree)

    def scale(self, x=1.0, y=1.0):
        return Shape(self.cross_section.scale((x, y)))

    def simplify(self, eps):
        return Shape(self.cross_section.simplify(eps))

    def to_polygons(self):
        return self.cross_section.to_polygons()

    def transform(self, matrix):
        return Shape(self.cross_section.transform(matrix))

    def move(self, x=0.0, y=0.0):
        return Shape(self.cross_section.translate((x, y)))

    def warp(self, xy_map_func):
        return Shape(self.cross_section.warp(xy_map_func))

    def warp_batch(self, xy_map_func):
        return Shape(self.cross_section.warp_batch(xy_map_func))


def get_circular_segments(radius):
    return manifold3d.get_circular_segments(radius)


def set_circular_segments(nseg):
    manifold3d.set_circular_segments(nseg)


def set_min_circular_angle(degrees):
    manifold3d.set_min_circular_angle(degrees)


def set_min_circular_edge_length(length):
    manifold3d.set_min_circular_edge_length(length)


def hull(*solids):
    mans = [s.manifold for s in solids]
    return Solid(Manifold.batch_hull(mans))


def hull_points(points):
    return Solid(Manifold.hull_points(points))


def hull2d(*shapes):
    sects = [s.cross_section for s in shapes]
    return Shape(CrossSection.batch_hull(sects))


def hull2d_points(points):
    return Shape(CrossSection.hull_points(points))


def cube(x=1.0, y=1.0, z=1.0, center=False):
    return Solid(Manifold.cube((x, y, z), center=center))


box = cube


def cylinder(h=1.0, d=1.0, r=None, r2=None, center=False, fn=0, outer=False):
    r = r or d / 2
    r2 = r if r2 is None else r2
    fn = fn or get_circular_segments(max(r, r2))
    s = 1.0 / np.cos(np.pi / fn) if outer else 1.0
    a = 180.0 / fn if outer else 0.0
    return Solid(
        Manifold.cylinder(h, r * s, r2 * s, circular_segments=fn, center=center)
    ).rotate(z=a)


def conic(h=1.0, d1=1.0, d2=1.0, r1=None, r2=None, center=False, fn=0, outer=False):
    r1 = r1 or d1 / 2
    r2 = r2 or d2 / 2
    fn = fn or get_circular_segments(max(r1, r2))
    s = 1.0 / np.cos(np.pi / fn) if outer else 1.0
    a = 180.0 / fn if outer else 0.0
    return Solid(
        Manifold.cylinder(h, r1 * s, r2 * s, circular_segments=fn, center=center)
    ).rotate(z=a)


def sphere(d=1.0, r=None, fn=0):
    r = r or d / 2
    return Solid(Manifold.sphere(r, fn))


def circle(d=1.0, r=None, fn=0, outer=False):
    r = r or d / 2
    fn = fn or get_circular_segments(r)
    s = 1.0 / np.cos(np.pi / fn) if outer else 1.0
    a = 180.0 / fn if outer else 0.0
    return Shape(CrossSection.circle(r * s, fn).rotate(a))


def square(x=1.0, y=1.0, center=False):
    return Shape(CrossSection.square((x, y), center=center))


def polygon(points, fill_rule="even_odd", relative=False):
    if relative:
        points = np.cumsum(np.asarray(points), axis=0)
    if fill_rule == "even_odd":
        fill_rule = manifold3d.FillRule.EvenOdd
    elif fill_rule == "negative":
        fill_rule = manifold3d.FillRule.Negative
    elif fill_rule == "non_zero":
        fill_rule = manifold3d.FillRule.NonZero
    elif fill_rule == "positive":
        fill_rule = manifold3d.FillRule.Positive
    else:
        raise ValueError(f"{fill_rule=}")
    return Shape(CrossSection([points], fillrule=fill_rule))


def cross_section(solid, z=0.0):
    return Shape(solid.manifold.slice(z))


def text(t, size=10.0, font="Helvetica", fn=8):
    polys = svg2polygons(text2svg(t, size=size, font=font), fn=fn)
    return Shape(CrossSection(polys, fillrule=manifold3d.FillRule.EvenOdd)).mirror(y=1)


def threads(
    d=8.0,
    h=8.0,
    pitch=1.0,
    depth_ratio=0.6,
    trap_scale=1.0,
    starts=1,
    fn=0,
    pitch_fn=8,
    lefty=False,
):
    fn = fn or get_circular_segments(d / 2)
    d2 = d - depth_ratio * 2 * pitch
    poly = circle(r=d / 2, fn=fn)
    solid = poly.extrude(h, fn=int(h / pitch * pitch_fn))

    def warp(pts):
        x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
        tz = -z / pitch / starts
        txy = np.arctan2(y, x) / (2 * np.pi)
        c = (txy - tz) * starts % 1
        c = np.abs(c - 0.5) * 2
        if trap_scale > 1:
            c = np.clip(0.5 + (c - 0.5) * trap_scale, 0, 1)
        s = 1 - (d - d2) / d * c
        x *= s
        y *= s
        return pts

    m = solid.warp_batch(warp)
    return m if lefty else m.mirror(x=1)


def load_stl(filename=None, data=None):
    if data is None:
        assert isinstance(filename, str)
        with open(filename, "rb") as f:
            data = f.read()
    data = np.frombuffer(data[84:], stl_dtype)
    verts = data["vert"].reshape(-1, 3)
    idx = np.arange(verts.shape[0]).reshape(-1, 3)
    m = Mesh(verts, idx)
    m.merge()
    return Solid(Manifold(m))


def _solid_to_cq(solid):
    import tempfile

    import cadquery as cq
    from cadquery.occ_impl.shapes import downcast
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    from OCP.StlAPI import StlAPI_Reader
    from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Shape

    with tempfile.NamedTemporaryFile(suffix=".stl") as f:
        f.write(solid.stl())
        f.flush()
        shape = TopoDS_Shape()
        if not StlAPI_Reader().Read(shape, f.name) or shape.IsNull():
            raise ValueError("Could not convert b4dcad STL mesh to a CadQuery shape")

    sewing = BRepBuilderAPI_Sewing(1e-6)
    sewing.Add(shape)
    sewing.Perform()
    sewed = downcast(sewing.SewedShape())

    solids = []
    if sewed.ShapeType() == TopAbs_SOLID:
        solids.append(cq.Solid.cast(sewed))
    else:
        shells = TopExp_Explorer(sewed, TopAbs_SHELL)
        while shells.More():
            shell = downcast(shells.Current())
            solids.append(cq.Solid.cast(BRepBuilderAPI_MakeSolid(shell).Solid()))
            shells.Next()

    if not solids:
        raise ValueError("Could not sew b4dcad STL mesh into a CadQuery solid")

    unified_solids = []
    for solid in solids:
        unify = ShapeUpgrade_UnifySameDomain(solid.wrapped, True, True, True)
        unify.SetLinearTolerance(1e-5)
        unify.SetAngularTolerance(1e-5)
        unify.AllowInternalEdges(False)
        unify.Build()
        unified_solids.append(cq.Solid.cast(unify.Shape()))

    return cq.Workplane().newObject(unified_solids)


def to_cq(obj):
    """Convert a b4dcad Solid or manifold3d Manifold to a CadQuery Workplane."""
    if isinstance(obj, Manifold):
        obj = Solid(obj)
    if not isinstance(obj, Solid):
        raise TypeError(
            f"to_cq expects a b4dcad Solid or manifold3d Manifold, got {type(obj).__name__}"
        )
    return obj.to_cq()


def from_cq(obj, tolerance=CQ_TESSELLATION_TOLERANCE):
    """Convert a CadQuery Workplane to a b4dcad Solid.

    The conversion tessellates the CadQuery OCCT shape into triangle meshes, so
    it is suitable for b4dcad/Manifold booleans and STL workflows, but it does
    not preserve OCCT faces, edges, or parametric CAD topology.

    CadQuery is imported lazily here so normal b4dcad scripts do not pay its
    import cost unless this bridge is actually used.

    Args:
        obj: CadQuery Workplane to convert.
        tolerance: Linear tessellation tolerance passed to CadQuery.
    """
    import cadquery as cq

    if not isinstance(obj, cq.Workplane):
        raise TypeError(
            f"from_cq expects a CadQuery Workplane, got {type(obj).__name__}"
        )

    verts = []
    tris = []
    for shape in obj.vals():
        shape_verts, shape_tris = shape.tessellate(tolerance)
        offset = len(verts)
        verts.extend(v.toTuple() for v in shape_verts)
        tris.extend(tuple(i + offset for i in tri) for tri in shape_tris)

    if not verts:
        return Solid()

    mesh = Mesh(np.array(verts, np.float32), np.array(tris, np.int32))
    mesh.merge()
    return Solid(Manifold(mesh))


set_circular_segments(64)  # set default


def _flat(items):
    # Accept positional objects and a single iterable, including generators.
    if len(items) == 1 and not isinstance(items[0], (Solid, Shape)):
        items = tuple(items[0])
    else:
        items = tuple(items)
    if items:
        kind = type(items[0])
        if not isinstance(items[0], (Solid, Shape)) or any(
            type(x) is not kind for x in items
        ):
            raise TypeError("batch booleans require all Solids or all Shapes")
    return items


def _batch(items, op):
    items = _flat(items)
    if not items:
        return Solid()
    if isinstance(items[0], Shape):
        return Shape(CrossSection.batch_boolean([s.cross_section for s in items], op))
    return Solid(Manifold.batch_boolean([s.manifold for s in items], op))


def union(*items):
    """Join many Solids (or many Shapes) in one batch operation.

    For Shapes, this is much faster than a + b + c + ... in a loop. For
    Solids the gain is smaller, because manifold already collects a chain
    of + operations. Give the items as arguments or as one list. Empty items are ignored, and no items give
    an empty Solid."""
    return _batch(items, manifold3d.OpType.Add)


def difference(base, *cutters):
    """Remove many Solids (or many Shapes) from `base` in one batch
    operation. The cutters can be arguments or one list."""
    if base.is_empty():
        return base
    return _batch([base, *_flat(cutters)], manifold3d.OpType.Subtract)


def intersection(*items):
    """Intersect homogeneous Solids or Shapes; an empty operand empties the result."""
    return _batch(items, manifold3d.OpType.Intersect)


def loft(shapes, heights, match="arc"):
    """One closed Solid through 2D Shapes, each at its z in `heights`
    (in increasing order).

    Each Shape must be one outline with no holes. The point counts can be
    different. The first points of the layers are matched by distance, and
    the sides are stitched by arc length (match='arc') or by the shorter
    diagonal at each step (match='shortest'). Layers of a similar form
    give the best result: offsets of one outline, or a square to a
    circle. The result is one mesh, with no faces between the layers."""
    if len(shapes) != len(heights) or len(shapes) < 2:
        raise ValueError("loft needs two or more shapes, and one height for each")
    if not np.all(np.isfinite(heights)) or any(
        z1 <= z0 for z0, z1 in zip(heights, heights[1:])
    ):
        raise ValueError("loft heights must increase")
    rings = [_loft_ring(shapes[0].to_polygons())]
    for sh in shapes[1:]:
        b = _loft_ring(sh.to_polygons())
        k = int(np.argmin(np.linalg.norm(b - rings[-1][0], axis=1)))
        rings.append(np.roll(b, -k, axis=0))
    offsets = np.concatenate([[0], np.cumsum([len(r) for r in rings])])
    verts = np.vstack(
        [
            np.column_stack([r, np.full(len(r), float(z))])
            for r, z in zip(rings, heights)
        ]
    )
    tris = []
    for k in range(len(rings) - 1):
        tris += _loft_stitch(rings[k], rings[k + 1], offsets[k], offsets[k + 1], match)
    bottom = manifold3d.triangulate([rings[0]])[:, ::-1] + offsets[0]
    top = manifold3d.triangulate([rings[-1]]) + offsets[-2]
    tris = np.vstack([np.array(tris), bottom, top]).astype(np.uint64)
    m = Manifold(manifold3d.Mesh64(vert_properties=verts, tri_verts=tris))
    if m.status() != manifold3d.Error.NoError:
        raise ValueError(f"loft failed: {m.status().name}")
    return Solid(m)


def _frames(path, closed, x_axis):
    # rotation minimising frames along the path (double reflection method)
    p = np.asarray(path, dtype=float)
    minimum = 3 if closed else 2
    if p.ndim != 2 or p.shape[1] != 3 or len(p) < minimum or not np.all(np.isfinite(p)):
        raise ValueError(f"path needs at least {minimum} finite 3D points")
    d = np.diff(np.vstack([p, p[:1]]) if closed else p, axis=0)
    lengths = np.linalg.norm(d, axis=1)
    if np.any(lengths == 0):
        raise ValueError(
            "path must not contain consecutive duplicate points (omit the repeated endpoint for a closed path)"
        )
    d /= lengths[:, None]
    if closed:
        t = d + np.roll(d, 1, axis=0)
    else:
        t = np.vstack([d[:1], d[:-1] + d[1:], d[-1:]])
    lengths = np.linalg.norm(t, axis=1)
    if np.any(lengths < 1e-12):
        raise ValueError("path must not reverse direction at a vertex")
    t /= lengths[:, None]
    if x_axis is None:
        x_axis = (1, 0, 0) if abs(t[0][2]) > 0.9 else (0, 0, 1)
    r = np.array(x_axis, dtype=float)
    r = r - (r @ t[0]) * t[0]
    if np.linalg.norm(r) < 1e-9:
        raise ValueError("x_axis is parallel to the start of the path")
    r /= np.linalg.norm(r)
    pts = np.vstack([p, p[:1]]) if closed else p
    tt = np.vstack([t, t[:1]]) if closed else t
    rs = [r]
    for i in range(len(pts) - 1):
        v1 = pts[i + 1] - pts[i]
        c1 = v1 @ v1
        rl = rs[-1] - (2 / c1) * (v1 @ rs[-1]) * v1
        tl = tt[i] - (2 / c1) * (v1 @ tt[i]) * v1
        v2 = tt[i + 1] - tl
        c2 = v2 @ v2
        rn = rl - (2 / c2) * (v2 @ rl) * v2 if c2 > 1e-18 else rl
        rs.append(rn / np.linalg.norm(rn))
    rs = np.array(rs)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)]) / seg.sum()
    if closed:
        # the frame does not come back to its start: spread the error
        b0 = np.cross(t[0], rs[0])
        err = np.arctan2(rs[-1] @ b0, rs[-1] @ rs[0])
        rs, s = rs[:-1], s[:-1]
    else:
        err = 0.0
    return p, t, rs, s, err


def sweep(shape, path, twist=0, closed=False, x_axis=None):
    """Move the 2D `shape` along a 3D polyline and return the Solid it
    fills.

    path is an (N, 3) array of points. At each point, the shape lies in
    the plane at right angles to the path, with its (0, 0) on the path.
    The frame turns as little as it can along the path (a rotation
    minimising frame), so a helix or a bent tube does not twist. Shape +x
    starts along `x_axis` (default: world z, or world x if the path
    starts along z), the same as Solid.orient(). `twist` adds a turn of
    that many degrees from the start to the end. With closed=True, the
    last point joins the first and the result has no end faces.

    The shape can have holes. Use many points on a curve, and keep the
    bend radius larger than the shape, or the sides cut through each
    other."""
    if shape.is_empty():
        raise ValueError("sweep needs a nonempty shape")
    if not np.isfinite(twist) or (
        closed and not np.isclose(twist / 360, round(twist / 360))
    ):
        raise ValueError("closed sweep twist must be a multiple of 360 degrees")
    p, t, r, s, err = _frames(path, closed, x_axis)
    polys = [np.asarray(q, dtype=float) for q in shape.to_polygons()]
    flat = np.vstack(polys)
    m = len(flat)
    n = len(p)
    ang = np.radians(twist) * s - err * s
    verts = np.empty((n * m, 3))
    for i in range(n):
        b = np.cross(t[i], r[i])
        c, sn = np.cos(ang[i]), np.sin(ang[i])
        u, v = c * r[i] + sn * b, -sn * r[i] + c * b
        verts[i * m : (i + 1) * m] = p[i] + flat[:, :1] * u + flat[:, 1:] * v
    starts = np.concatenate([[0], np.cumsum([len(q) for q in polys])])
    tris = []
    rings = n if closed else n - 1
    for i in range(rings):
        a, b = i * m, ((i + 1) % n) * m
        for c0, c1 in zip(starts[:-1], starts[1:]):
            for k in range(c0, c1):
                k1 = k + 1 if k + 1 < c1 else c0
                tris += [(a + k, a + k1, b + k), (a + k1, b + k1, b + k)]
    tris = np.array(tris, dtype=np.uint64)
    if not closed:
        cap = manifold3d.triangulate(polys).astype(np.uint64)
        tris = np.vstack([tris, cap[:, ::-1], cap + (n - 1) * m])
    out = Manifold(manifold3d.Mesh64(vert_properties=verts, tri_verts=tris))
    if out.status() != manifold3d.Error.NoError:
        raise ValueError(f"sweep failed: {out.status().name}")
    return Solid(out)


def mesh(verts, tris, merge=True):
    """A Solid from a triangle mesh: verts is an (N, 3) array of points and
    tris an (M, 3) array of vertex indices.

    With merge=True, vertices at the same position are joined first, so a
    mesh with duplicate vertices (one set for each triangle, as in STL)
    still closes. If the triangles wind clockwise (seen from outside), the
    winding is turned over. Raises ValueError if the mesh is not a closed
    manifold."""
    v = np.asarray(verts, dtype=np.float64)
    indices = np.asarray(tris)
    if v.ndim != 2 or v.shape[1] != 3 or not np.all(np.isfinite(v)):
        raise ValueError("verts must be a finite (N, 3) array")
    if (
        indices.ndim != 2
        or indices.shape[1] != 3
        or not np.issubdtype(indices.dtype, np.integer)
    ):
        raise ValueError("tris must be an integer (M, 3) array")
    if np.any(indices < 0) or np.any(indices >= len(v)):
        raise ValueError("triangle indices are out of bounds")
    v = np.ascontiguousarray(v)
    t = np.ascontiguousarray(indices, dtype=np.uint64)
    m = manifold3d.Mesh64(vert_properties=v, tri_verts=t)
    if merge:
        m.merge()
    solid = Manifold(m)
    if solid.status() != manifold3d.Error.NoError:
        raise ValueError(f"mesh is not a closed manifold: {solid.status().name}")
    if solid.volume() < 0:
        m = manifold3d.Mesh64(
            vert_properties=v, tri_verts=np.ascontiguousarray(t[:, ::-1])
        )
        if merge:
            m.merge()
        solid = Manifold(m)
    return Solid(solid)


def level_set(sdf, bounds, edge_length, level=0, tolerance=-1):
    """A Solid from a signed distance function (marching tetrahedra).

    sdf(x, y, z) returns a float: positive inside, negative outside. (Many
    SDF formulas use the opposite sign; give lambda x, y, z: -f(x, y, z).)
    bounds is (xmin, ymin, zmin, xmax, ymax, zmax), the same order as
    Solid.bounding_box(). edge_length sets the grid size, and with it the
    detail and the time. A positive `level` insets the surface, and a
    negative one outsets it.

    sdf is called once for each grid point, so keep it fast."""
    return Solid(Manifold.level_set(sdf, list(bounds), edge_length, level, tolerance))


def dxf(filename=None, data=None, fn=64, on_unknown=None, spline_tol=0.005):
    """Load a DXF file (LINE, ARC, CIRCLE, LWPOLYLINE, SPLINE) as a Shape.
    Loops inside other loops become holes (even-odd fill).
    Other entity types raise ValueError; see dxf2polygons for on_unknown."""
    if data is None:
        with open(filename, encoding="latin-1") as f:
            data = f.read()
    polys = dxf2polygons(data, fn=fn, on_unknown=on_unknown, spline_tol=spline_tol)
    return Shape(CrossSection(polys, fillrule=manifold3d.FillRule.EvenOdd))


def _hex_color(color):
    if color is None:
        return None
    if isinstance(color, str):
        c = color.lstrip("#")
        if len(c) not in (6, 8):
            raise ValueError(f"color must be #RRGGBB or #RRGGBBAA, not {color!r}")
        try:
            int(c, 16)
        except ValueError as exc:
            raise ValueError("color must contain hexadecimal digits") from exc
        return "#" + c.upper()
    color = tuple(color)
    if len(color) not in (3, 4) or not all(np.isfinite(v) for v in color):
        raise ValueError("color must have three or four finite channels")
    rgba = [int(round(255 * min(max(float(v), 0), 1))) for v in color]
    return "#" + "".join(f"{v:02X}" for v in rgba)


def save_3mf(filename, parts):
    """Write solids to a 3MF file (3MF core spec), each one its own object,
    so that a slicer can place, colour or assign them one by one.

    parts is a list of Solids, or of (name, Solid) or (name, Solid, color).
    color is '#RRGGBB', '#RRGGBBAA', or an (r, g, b[, a]) tuple of 0-1
    floats. The vertices are written in 64-bit precision, so the mesh
    stays closed. Empty solids are skipped. Without a filename, return
    the file as bytes."""
    objects, colors = [], []
    for k, part in enumerate(parts, start=1):
        if isinstance(part, Solid):
            part = (f"part {k}", part, None)
        name, solid, color = (tuple(part) + (None,))[:3]
        if solid.is_empty():
            continue
        objects.append((name, solid, _hex_color(color)))
        if objects[-1][2] is not None:
            colors.append(objects[-1][2])
    xml = io.StringIO()
    xml.write(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        "<resources>"
    )
    if colors:
        xml.write('<basematerials id="1">')
        for c in colors:
            xml.write(f'<base name={quoteattr(c)} displaycolor="{c}"/>')
        xml.write("</basematerials>")
    oid, cidx, ids = 1, 0, []
    for name, solid, color in objects:
        oid += 1
        ids.append(oid)
        mat = ""
        if color is not None:
            mat = f' pid="1" pindex="{cidx}"'
            cidx += 1
        mesh = solid.manifold.to_mesh64()
        xml.write(
            f'<object id="{oid}" name={quoteattr(str(name))} type="model"{mat}><mesh><vertices>'
        )
        np.savetxt(
            xml,
            np.asarray(mesh.vert_properties)[:, :3],
            fmt='<vertex x="%.17g" y="%.17g" z="%.17g"/>',
            newline="",
        )
        xml.write("</vertices><triangles>")
        np.savetxt(
            xml,
            np.asarray(mesh.tri_verts),
            fmt='<triangle v1="%d" v2="%d" v3="%d"/>',
            newline="",
        )
        xml.write("</triangles></mesh></object>")
    xml.write("</resources><build>")
    for i in ids:
        xml.write(f'<item objectid="{i}"/>')
    xml.write("</build></model>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _3MF_TYPES)
        z.writestr("_rels/.rels", _3MF_RELS)
        z.writestr("3D/3dmodel.model", xml.getvalue())
    if filename is None:
        return buf.getvalue()
    with open(filename, "wb") as f:
        f.write(buf.getvalue())


_3MF_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)
_3MF_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    "</Relationships>"
)
