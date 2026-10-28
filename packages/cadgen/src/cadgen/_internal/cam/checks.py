"""The checks: what cadgen stands behind in a plan, before and after FreeCAD plans it.

FreeCAD plans what it is asked to and checks nothing against the part: asked to pocket a floor that lies under an
overhang, it cuts through the overhang. So every plan is checked here, and a plan with an error posts no G-code:

* before FreeCAD runs, :func:`reach`: every face an operation names can be seen from the setup's side (rays straight
  up the tool's axis from points on the face, each moved a hair off it along the face's outward normal, leave the
  part: a wall is seen, and the wall of a T-slot's undercut is not), every hole it drills opens toward the tool, its
  drill's point is no longer than the drawn one, and no cut goes deeper below the stock top than the tool's flutes;
* after, :func:`check_moves` on each setup's moves (:mod:`toolpath`, setup coordinates, the tool down -Z):
  ``gouge``, a move whose tool (its real profile: flat, ball, bull nose or drill point) comes into the part, tested
  against the part's height seen from above on a grid finer than the tool; ``rapid_into_stock``, a rapid into stock no
  move before it has cut (the stock lowered along each cutting move, in program order); ``empty_operation``, an
  operation FreeCAD planned no cut for; ``uncut``, a face or hole an operation names that no cutting move reaches at
  the operation's final depth (FreeCAD skips what its tool does not fit, and says nothing); ``travel``, a setup whose
  moves span more than the machine travels.

The height grid sees the part from above, so it is exact for a part machined from one side and conservative for an
undercut (a tool under an overhang is a hit). A finding is a dict of ``check``, ``severity``, ``type``, ``summary``,
``description`` and ``items``. Stdlib at import; numpy inside.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

__all__ = ["CHECKS", "Target", "by_severity", "check_moves", "face_points", "finding", "reach"]

#: Each check and the finding types it raises.
CHECKS = {
    "job": ("job", "freecad", "internal_error"),
    "reach": ("unreachable", "too_deep", "not_facing", "drill_size", "drill_point"),
    "gouge": ("gouge",),
    "rapid_into_stock": ("rapid_into_stock",),
    "empty_operation": ("empty_operation",),
    "coverage": ("uncut",),
    "travel": ("travel",),
    "limits": ("clamped",),
}
_SEVERITY = {"error": 0, "warning": 1, "info": 2}
#: How far a move may come into the part before it is a gouge (mm): the tessellation's and the grid's own error.
GOUGE_TOLERANCE_MM = 0.02
#: How far a rapid may be below the stock top before it is a hit (mm).
RAPID_TOLERANCE_MM = 0.05
#: The most cells the height grid holds: a part over a metre wide checks on a coarser grid, never a full memory.
MAX_CELLS = 16_000_000
#: How far off a face its reach is judged from, along its outward normal (mm): past the tessellation's own error, and
#: far less than any overhang a tool could not pass.
NUDGE_MM = 0.05
#: How far above an operation's final depth a move still counts as cutting at it (mm).
DEPTH_TOLERANCE_MM = 0.05
#: How much longer than the drawn point a drill's may be before the hole is short of its diameter at the bottom (mm).
POINT_TOLERANCE_MM = 0.01


@dataclass(frozen=True)
class Target:
    """What one operation must reach, at its final depth ``final_z`` (setup coordinates): a named face (``points`` on
    it, (K, 3)) that a cutting move's tool must come over (``wall``: come up beside, within its radius and
    ``leave_mm``), or a hole (``axis_xy``) a cutting move must plunge down to that depth."""

    ref: str
    final_z: float
    points: Any = None
    axis_xy: tuple[float, float] | None = None
    wall: bool = False
    leave_mm: float = 0.0


def finding(check: str, severity: str, kind: str, summary: str, description: str = "", items=()) -> dict:
    if kind not in CHECKS[check] or severity not in _SEVERITY:
        raise ValueError(f"finding {check}/{kind} ({severity}) is not one cadgen raises")
    return {"check": check, "severity": severity, "type": kind, "summary": summary, "description": description,
            "items": list(items)}


def by_severity(found) -> list[dict]:
    """Errors first, then warnings, then notes; stable within each."""
    return sorted(found, key=lambda item: _SEVERITY[item["severity"]])


def _xyz(point) -> list[float]:
    return [round(float(c), 3) + 0.0 for c in point]


def _rays_blocked(vertices, triangles, origins):
    """For each origin, whether a ray from it straight up (+Z) meets the mesh."""
    import numpy as np

    tri = np.asarray(vertices, dtype=float)[np.asarray(triangles)]
    out = np.zeros(len(origins), dtype=bool)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    det = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    keep = np.abs(det) > 1e-12
    a, b, c, det = a[keep], b[keep], c[keep], det[keep]
    for k, (x, y, z) in enumerate(origins):
        w1 = ((x - a[:, 0]) * (c[:, 1] - a[:, 1]) - (y - a[:, 1]) * (c[:, 0] - a[:, 0])) / det
        w2 = ((b[:, 0] - a[:, 0]) * (y - a[:, 1]) - (b[:, 1] - a[:, 1]) * (x - a[:, 0])) / det
        inside = (w1 >= -1e-9) & (w2 >= -1e-9) & (w1 + w2 <= 1 + 1e-9)
        at = a[:, 2] + w1 * (b[:, 2] - a[:, 2]) + w2 * (c[:, 2] - a[:, 2])
        out[k] = bool(np.any(inside & (at > z + 1e-6)))
    return out


def face_points(vertices, triangles, mine, step: float, most: int = 2000, nudge: float = 0.0):
    """Points spread over the triangles ``mine`` selects, about ``step`` apart (at most ``most``), each ``nudge`` off
    its triangle along the triangle's outward normal (the tessellation is wound outward)."""
    import numpy as np

    tri = np.asarray(vertices, dtype=float)[np.asarray(triangles)[mine]]
    points = []
    for a, b, c in tri:
        normal = np.cross(b - a, c - a)
        length = float(np.linalg.norm(normal))
        off = normal * (nudge / length) if length > 1e-15 else np.zeros(3)
        n = max(1, int(math.ceil(max(np.linalg.norm(b - a), np.linalg.norm(c - a), np.linalg.norm(c - b)) / step)))
        for i in range(n + 1):
            for j in range(n + 1 - i):
                w1, w2 = (i + 1 / 3) / (n + 1), (j + 1 / 3) / (n + 1)
                points.append(a + w1 * (b - a) + w2 * (c - a) + off)
    points = np.asarray(points).reshape(-1, 3)
    if len(points) > most:
        points = points[np.linspace(0, len(points) - 1, most).astype(int)]
    return points


def reach(op, faces, holes, mesh, face_of_triangle, tool, stock_top: float, depth_z: float | None,
          walls=None) -> list[dict]:
    """The reach findings of one operation, in setup coordinates (``mesh``: the part's ``(vertices, triangles)``
    there; ``faces``: the :class:`FaceInfo` of each face it names, their normals turned into the setup; ``holes``: the
    feature reader's hole for each face of a hole, its ``reach`` turned into the setup's words; ``walls``: for a flat
    face an inside profile names, the walls it would mean). A drill naming two faces of one hole drills it once, and
    is judged once. An inside profile runs along walls: a flat face square to the tool gives it nothing to run along
    (FreeCAD traces its outline's inside, cutting nothing of the part), so it is refused, naming its walls."""
    import numpy as np

    found = []
    vertices, triangles = mesh
    drilled: set[str] = set()
    for info in faces:
        where = {"operation": op.index, "face": info.ref}
        if op.op == "drill":
            hole = holes.get(info.ref)
            if hole is None:
                found.append(finding("reach", "error", "not_facing", f"{op.where}: {info.ref} is not a face of a hole",
                                     "drill names a face of a hole (its wall, its counterbore's, its floor), as "
                                     "cadgen cam features lists them", [where]))
                continue
            if hole["faces"][0] in drilled:
                continue
            drilled.add(hole["faces"][0])
            if "+Z" not in hole["reach"]:
                found.append(finding("reach", "error", "unreachable",
                                     f"{op.where}: the hole {info.ref} does not open toward the tool in this setup",
                                     "drill it from a setup the hole opens toward (its reach: "
                                     f"{', '.join(hole['reach']) or 'none'})", [where]))
            elif abs(tool.diameter_mm - hole["diameter_mm"]) > 0.01:
                larger = tool.diameter_mm > hole["diameter_mm"]
                found.append(finding(
                    "reach", "error" if larger else "warning", "drill_size",
                    f"{op.where}: the hole {info.ref} is Ø{hole['diameter_mm']:g} and {tool.id} is a "
                    f"Ø{tool.diameter_mm:g} drill", "a drill larger than its hole cuts into the part: drill it with "
                    "one its size" if larger else "it leaves the hole under size: open it out after (a pocket, or "
                    "a drill its size), or drill it with one its size", [where]))
            drawn = float(hole.get("point_mm", 0.0))
            if "+Z" in hole["reach"] and not hole["through"] and tool.tip_length_mm > drawn + POINT_TOLERANCE_MM:
                short = tool.tip_length_mm - drawn
                found.append(finding(
                    "reach", "warning", "drill_point",
                    f"{op.where}: {tool.id}'s point is {tool.tip_length_mm:.2f} mm long and the hole {info.ref} is "
                    + ("drawn with a flat floor" if drawn == 0 else f"drawn with a {drawn:.2f} mm point")
                    + f": its bottom {short:.2f} mm is left a cone, short of Ø{hole['diameter_mm']:g}",
                    "the drill stops at the drawn bottom, so its longer point leaves material there: mill the floor "
                    "after (a pocket of it with an end mill that fits), or drill with the drawn point angle",
                    [where]))
            continue
        normal = info.normal
        if op.op == "profile" and op.params["side"] == "inside" and normal is not None and abs(normal[2]) > 1 - 1e-6:
            named = (walls or {}).get(info.ref) or []
            found.append(finding(
                "reach", "error", "not_facing",
                f"{op.where}: an inside profile runs along walls, and {info.ref} is a flat face"
                + (f"; the walls round it: {', '.join(named)}" if named else ""),
                "name the walls of the hole, bore, slot or pocket to cut, as cadgen cam features lists them: "
                "inside a flat face FreeCAD traces its outline and cuts nothing of the part", [where]))
            continue
        if op.op in ("pocket", "adaptive", "face") and (normal is None or normal[2] < 1 - 1e-6):
            found.append(finding("reach", "error", "not_facing",
                                 f"{op.where}: {info.ref} is not a flat face looking up at the tool in this setup",
                                 f"{op.op} names the floors it clears down to, flat and facing the tool", [where]))
            continue
        origins = face_points(vertices, triangles, face_of_triangle == info.ordinal, max(0.25, tool.radius / 2),
                              nudge=NUDGE_MM)
        if not len(origins):
            continue
        blocked = _rays_blocked(vertices, triangles, origins)
        if blocked.any():
            share = blocked.mean()
            found.append(finding(
                "reach", "error", "unreachable",
                f"{op.where}: {share:.0%} of {info.ref} is under the part, seen from the tool's side",
                "a tool coming straight down cannot get under what overhangs it: it would cut through it. Machine "
                "that face from a setup that sees it, or name the faces the tool can reach",
                [dict(where, at=_xyz(origins[int(np.argmax(blocked))]))]))
    if depth_z is not None and stock_top - depth_z > tool.flute_length_mm + 1e-6:
        found.append(finding(
            "reach", "error", "too_deep",
            f"{op.where}: the cut goes {stock_top - depth_z:.2f} mm below the stock top, past {tool.id}'s "
            f"{tool.flute_length_mm:g} mm of flutes",
            "use a tool with longer flutes (state its flute_length_mm), or cut less deep from this side",
            [{"operation": op.index}]))
    return found


def _profile_height(tool, d):
    """The tool's lower surface above its tip at radial distance ``d`` (an array within its radius)."""
    import numpy as np

    r = tool.radius
    if tool.type == "ball":
        return r - np.sqrt(np.maximum(0.0, r * r - d * d))
    if tool.type == "bull":
        rc = tool.corner_radius_mm
        out = np.maximum(0.0, d - (r - rc))
        return rc - np.sqrt(np.maximum(0.0, rc * rc - out * out))
    if tool.type == "drill":
        return d / math.tan(math.radians(tool.point_angle / 2.0))
    return np.zeros_like(d)


def _height_grid(vertices, triangles, low, high, cell: float):
    """The part's highest point over each cell centre (``-inf`` where it has none), and the grid's origin."""
    import numpy as np

    nx = max(1, int(math.ceil((high[0] - low[0]) / cell)))
    ny = max(1, int(math.ceil((high[1] - low[1]) / cell)))
    top = np.full((nx, ny), -np.inf)
    tri = np.asarray(vertices, dtype=float)[np.asarray(triangles)]
    e1, e2 = tri[:, 1, :2] - tri[:, 0, :2], tri[:, 2, :2] - tri[:, 0, :2]
    det = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    keep = np.abs(det) > 1e-12
    tri, det = tri[keep], det[keep]
    lo = np.maximum(np.floor((tri[:, :, :2].min(axis=1) - low[:2]) / cell - 0.5).astype(int), 0)
    hi = np.minimum(np.ceil((tri[:, :, :2].max(axis=1) - low[:2]) / cell - 0.5).astype(int), [nx - 1, ny - 1])
    for t in range(len(tri)):
        (i0, j0), (i1, j1) = lo[t], hi[t]
        if i1 < i0 or j1 < j0:
            continue
        ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1), indexing="ij")
        px, py = low[0] + (ii + 0.5) * cell, low[1] + (jj + 0.5) * cell
        a, b, c = tri[t]
        w1 = ((px - a[0]) * (c[1] - a[1]) - (py - a[1]) * (c[0] - a[0])) / det[t]
        w2 = ((b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0])) / det[t]
        inside = (w1 >= -1e-9) & (w2 >= -1e-9) & (w1 + w2 <= 1 + 1e-9)
        if inside.any():
            z = a[2] + w1 * (b[2] - a[2]) + w2 * (c[2] - a[2])
            np.maximum.at(top, (ii[inside], jj[inside]), z[inside])
    return top


def _dense(points, step: float):
    """The polyline through ``points`` with no gap over ``step``: each segment split evenly."""
    import numpy as np

    if len(points) < 2:
        return points
    gaps = np.linalg.norm(np.diff(points, axis=0), axis=1)
    counts = np.maximum(1, np.ceil(gaps / step).astype(int))
    pieces = [points[:1]]
    for k, n in enumerate(counts):
        f = (np.arange(1, n + 1) / n)[:, None]
        pieces.append(points[k] + f * (points[k + 1] - points[k]))
    return np.vstack(pieces)


def _gouges(moves, select, tool, grid, low, cell: float):
    """The first sample of the moves ``select`` picks whose tool comes into the part, and how far: ``(point, depth)``
    or None."""
    import numpy as np

    keep = np.nonzero(select)[0]
    if not len(keep):
        return None
    worst = None
    # Each run of selected samples is one polyline; a move's start is the sample before it.
    runs = np.split(keep, np.nonzero(np.diff(keep) > 1)[0] + 1)
    nx, ny = grid.shape
    for run in runs:
        start = max(run[0] - 1, 0)
        points = _dense(moves.points[start:run[-1] + 1], cell)
        for chunk in range(0, len(points), 2000):
            here = points[chunk:chunk + 2000]
            i, j, height, inside = _disk(tool, cell, here, low)
            # Each cell centre's distance is from the tool's axis itself, not from the cell the axis is in; a cell at
            # the tool's very edge is the tessellation's to decide, not a gouge.
            d = np.hypot(low[0] + (i + 0.5) * cell - here[:, :1], low[1] + (j + 0.5) * cell - here[:, 1:2])
            inside &= (i >= 0) & (i < nx) & (j >= 0) & (j < ny) & (d <= tool.radius - GOUGE_TOLERANCE_MM)
            depth = np.where(inside, grid[np.clip(i, 0, nx - 1), np.clip(j, 0, ny - 1)] - height, -np.inf).max(axis=1)
            k = int(np.argmax(depth))
            if depth[k] > GOUGE_TOLERANCE_MM and (worst is None or depth[k] > worst[1]):
                worst = (here[k], float(depth[k]))
    return worst


def _cells(here, radius: float, low, cell: float):
    """For points ``here`` (P, 3): the grid cells round each out to ``radius`` and a little past, and each cell
    centre's distance from the point in XY, ``(i, j, d)`` each (P, K)."""
    import numpy as np

    span = int(math.ceil(radius / cell)) + 1
    offsets = np.arange(-span, span + 1)
    di, dj = (a.ravel() for a in np.meshgrid(offsets, offsets, indexing="ij"))
    i = np.floor((here[:, :1] - low[0]) / cell).astype(int) + di[None, :]
    j = np.floor((here[:, 1:2] - low[1]) / cell).astype(int) + dj[None, :]
    return i, j, np.hypot(low[0] + (i + 0.5) * cell - here[:, :1], low[1] + (j + 0.5) * cell - here[:, 1:2])


def _disk(tool, cell: float, here, low):
    """For points ``here`` (P, 3): the grid cells under the tool's end and the tool's lower surface over each,
    ``(i, j, height, inside)`` each (P, K): ``inside`` the cells within the tool's radius."""
    import numpy as np

    i, j, d = _cells(here, tool.radius, low, cell)
    return i, j, here[:, 2:3] + _profile_height(tool, np.minimum(d, tool.radius)), d <= tool.radius


def _uncut(moves, op, tool, targets, low, shape, cell: float) -> list[str]:
    """The refs of the ``targets`` (:class:`Target`) no cutting move of ``op`` reaches at its final depth: a hole no
    feed goes down the axis of, a floor no tool comes over, a wall no tool runs beside."""
    import numpy as np

    keep = np.nonzero((moves.op == op.index) & ~moves.rapid)[0]
    if not len(keep) or not targets:
        return []
    runs = np.split(keep, np.nonzero(np.diff(keep) > 1)[0] + 1)
    points = np.vstack([_dense(moves.points[max(run[0] - 1, 0):run[-1] + 1], cell) for run in runs])
    nx, ny = shape
    swept: dict = {}
    missed = []
    for target in targets:
        at = points[points[:, 2] <= target.final_z + DEPTH_TOLERANCE_MM]
        if target.axis_xy is not None:
            x, y = target.axis_xy
            if not len(at) or not np.any(np.hypot(at[:, 0] - x, at[:, 1] - y) <= DEPTH_TOLERANCE_MM):
                missed.append(target.ref)
            continue
        radius = tool.radius + (target.leave_mm + cell if target.wall else 0.0)
        key = (target.final_z, radius)
        if key not in swept:
            cells = np.zeros(shape, dtype=bool)
            for chunk in range(0, len(at), 2000):
                i, j, d = _cells(at[chunk:chunk + 2000], radius, low, cell)
                inside = (d <= radius) & (i >= 0) & (i < nx) & (j >= 0) & (j < ny)
                cells[i[inside], j[inside]] = True
            swept[key] = cells
        i = np.floor((target.points[:, 0] - low[0]) / cell).astype(int)
        j = np.floor((target.points[:, 1] - low[1]) / cell).astype(int)
        inside = (i >= 0) & (i < nx) & (j >= 0) & (j < ny)
        if not swept[key][i[inside], j[inside]].any():
            missed.append(target.ref)
    return missed


def _rapid_into_stock(moves, operations, tools, stock_low, stock_high, low, shape, cell: float):
    """The first rapid that runs into stock no move before it has cut: ``(operation, point, depth)`` or None.

    The stock is a height on the grid, its top to start with; each cutting move lowers it to the tool's lower surface
    along its way, in program order, so a rapid inside a pocket already cleared (FreeCAD's adaptive links passes so)
    is not a hit, and one through material is."""
    import numpy as np

    nx, ny = shape
    xs = low[0] + (np.arange(nx) + 0.5) * cell
    ys = low[1] + (np.arange(ny) + 0.5) * cell
    over = ((xs >= stock_low[0])[:, None] & (xs <= stock_high[0])[:, None]
            & (ys >= stock_low[1])[None, :] & (ys <= stock_high[1])[None, :])
    stock = np.where(over, float(stock_high[2]), -np.inf)
    tool_of = {op.index: tools[op.tool] for op in operations}
    # Runs of moves of one kind and one operation, each move by the sample it ends at.
    ends = np.arange(1, len(moves.points))
    change = (moves.rapid[2:] != moves.rapid[1:-1]) | (moves.op[2:] != moves.op[1:-1])
    for run in np.split(ends, np.nonzero(change)[0] + 1):
        if not len(run):
            continue
        tool = tool_of.get(int(moves.op[run[0]]))
        if tool is None:
            continue
        points = _dense(moves.points[run[0] - 1:run[-1] + 1], cell)
        for chunk in range(0, len(points), 2000):
            here = points[chunk:chunk + 2000]
            i, j, height, inside = _disk(tool, cell, here, low)
            inside &= (i >= 0) & (i < nx) & (j >= 0) & (j < ny)
            ii, jj = np.clip(i, 0, nx - 1), np.clip(j, 0, ny - 1)
            if moves.rapid[run[0]]:
                depth = np.where(inside, stock[ii, jj] - height, -np.inf).max(axis=1)
                k = int(np.argmax(depth))
                if depth[k] > RAPID_TOLERANCE_MM:
                    return int(moves.op[run[0]]), here[k], float(depth[k])
            else:
                np.minimum.at(stock, (ii[inside], jj[inside]), height[inside])
    return None


def check_moves(setup_id: str, moves, operations, tools, mesh, stock_low, stock_high, machine,
                targets=None) -> list[dict]:
    """The findings of one setup's moves (setup coordinates): ``operations`` its :class:`Operation` list, ``mesh``
    the part's ``(vertices, triangles)`` there, the stock's box from ``stock_low`` to ``stock_high``, ``targets``
    each operation's :class:`Target` list by its index."""
    import numpy as np

    found = []
    vertices, triangles = mesh
    moving = np.concatenate([[False], np.any(np.diff(moves.points, axis=0) != 0, axis=1)])
    for op in operations:
        mine = moves.op == op.index
        if not np.any(mine & moving & ~moves.rapid):
            found.append(finding("empty_operation", "error", "empty_operation",
                                 f"{op.where}: FreeCAD planned no cut for this {op.op}",
                                 "the faces it names give it nothing to cut with this tool: check the faces "
                                 "(cadgen cam features) and that the tool fits them", [{"operation": op.index}]))
    path_low = np.minimum(moves.points.min(axis=0), np.asarray(vertices).min(axis=0))
    path_high = np.maximum(moves.points.max(axis=0), np.asarray(vertices).max(axis=0))
    smallest = min(tools[op.tool].radius for op in operations)
    cell = max(0.05, min(0.5, smallest / 4.0))
    margin = max(tools[op.tool].radius for op in operations) + cell
    low, high = path_low - margin, path_high + margin
    area = (high[0] - low[0]) * (high[1] - low[1])
    if area / cell ** 2 > MAX_CELLS:
        cell = math.sqrt(area / MAX_CELLS)
    grid = _height_grid(vertices, triangles, low, high, cell)
    for op in operations:
        what = "drilled nothing down" if op.op == "drill" else "cut nothing at the operation's depth over"
        for ref in _uncut(moves, op, tools[op.tool], (targets or {}).get(op.index, ()), low, grid.shape, cell):
            found.append(finding(
                "coverage", "error", "uncut", f"{op.where}: FreeCAD {what} {ref}",
                "FreeCAD leaves out what the tool does not fit, and says nothing: a floor, slot or hole narrower "
                "than the tool, a hole it did not take. Cut it in its own operation with a tool that fits it, or "
                "leave it out of this one", [{"setup": setup_id, "operation": op.index, "face": ref}]))
    for op in operations:
        mine = moves.op == op.index
        hit = _gouges(moves, mine, tools[op.tool], grid, low, cell)
        if hit is not None:
            point, depth = hit
            found.append(finding(
                "gouge", "error", "gouge",
                f"{op.where}: the tool comes {depth:.2f} mm into the part",
                "FreeCAD's path for this operation runs the tool into the part (often under an overhang it was "
                "not told about): name only faces the tool can reach, or use a smaller tool",
                [{"setup": setup_id, "operation": op.index, "at": _xyz(point)}]))
    hit = _rapid_into_stock(moves, operations, tools, stock_low, stock_high, low, grid.shape, cell)
    if hit is not None:
        k, point, depth = hit
        found.append(finding("rapid_into_stock", "error", "rapid_into_stock",
                             f"setup {setup_id}: a rapid runs {depth:.2f} mm into stock not yet cut",
                             "a rapid at full speed through material breaks the tool: raise retract_mm, or cut that "
                             "material first", [{"setup": setup_id, "operation": k, "at": _xyz(point)}]))
    if machine.travel_mm is not None:
        span = moves.points.max(axis=0) - moves.points.min(axis=0)
        over = [f"{axis} {s:.1f} mm of {t:g}" for axis, s, t in zip("XYZ", span, machine.travel_mm) if s > t + 1e-6]
        if over:
            found.append(finding("travel", "error", "travel",
                                 f"setup {setup_id}: the moves span more than the machine travels ({', '.join(over)})",
                                 "the part does not fit this machine in this setup", [{"setup": setup_id}]))
    return found
