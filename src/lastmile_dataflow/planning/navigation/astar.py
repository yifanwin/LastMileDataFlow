"""Eight-neighbor A* with no corner cutting and metric distance fields."""

from dataclasses import dataclass
import heapq
import math

import mujoco
import numpy as np
from scipy.ndimage import distance_transform_edt

from ...scenes.geometry import geom_points
from lastmile_dataflow.scenes.geometry import room_triangles
from lastmile_dataflow.scenes.tasks.discovery import task_support_obstacles

# Height cleared by chassis-only navigation (the arm is held above it).
CHASSIS_HEIGHT_M = 0.40


@dataclass
class Grid:
    origin: np.ndarray
    resolution: float
    free: np.ndarray
    support_obstacles: tuple = ()

    def cell(self, xy):
        x, y = np.rint((np.asarray(xy) - self.origin) / self.resolution).astype(int)
        if 0 <= y < self.free.shape[0] and 0 <= x < self.free.shape[1]:
            return (int(y), int(x))
        return None

    def xy(self, cell):
        y, x = cell
        return self.origin + np.array([x, y]) * self.resolution

    def neighbors(self, cell):
        y, x = cell
        for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            yy, xx = y + dy, x + dx
            if not (0 <= yy < self.free.shape[0] and 0 <= xx < self.free.shape[1]) or not self.free[yy, xx]:
                continue
            if dy and dx and (not self.free[y, xx] or not self.free[yy, x]):
                continue
            yield (yy, xx), self.resolution * (math.sqrt(2) if dx and dy else 1.0)


def _dijkstra(grid, start, limit=math.inf):
    """Metric distances from start over free cells, and the parent of each reached cell."""
    distance = {start: 0.0}
    parent = {}
    queue = [(0.0, start)]
    while queue:
        cost, node = heapq.heappop(queue)
        if cost > distance[node] + 1e-10:
            continue
        for neighbor, step in grid.neighbors(node):
            value = cost + step
            if value <= limit and value + 1e-10 < distance.get(neighbor, math.inf):
                distance[neighbor] = value
                parent[neighbor] = node
                heapq.heappush(queue, (value, neighbor))
    return distance, parent


def distance_field(grid, source_xy, limit):
    distances = np.full(grid.free.shape, np.inf)
    start = grid.cell(source_xy)
    if start is None or not grid.free[start]:
        return distances
    for cell, value in _dijkstra(grid, start, limit)[0].items():
        distances[cell] = value
    return distances


def segment_free(grid, start_xy, end_xy):
    """Check short endpoint connectors using the same no-corner-cutting grid."""
    length = float(np.linalg.norm(np.asarray(end_xy) - start_xy))
    count = max(1, math.ceil(length / (grid.resolution / 4)))
    previous = None
    for xy in np.linspace(start_xy, end_xy, count + 1):
        cell = grid.cell(xy)
        if cell is None or not grid.free[cell]:
            return False
        if previous is not None and cell != previous:
            if cell not in {node for node, _ in grid.neighbors(previous)}:
                return False
        previous = cell
    return True


def search_region(grid, start_xy, goals):
    """Dijkstra to a finite region approximation, including exact anchors.

    Goals carry xy/yaw. All headings fit the held-body swept envelope used to
    construct the grid; yaw does not contribute to the translation metric.
    """
    start = grid.cell(start_xy)
    if start is None or not segment_free(grid, start_xy, grid.xy(start)):
        return None
    distance, parent = _dijkstra(grid, start)
    reachable = []
    start_offset = float(np.linalg.norm(np.asarray(start_xy) - grid.xy(start)))
    for goal in goals:
        cell = grid.cell(goal["xy"])
        if cell not in distance or not segment_free(grid, grid.xy(cell), goal["xy"]):
            continue
        length = start_offset + distance[cell] + float(np.linalg.norm(grid.xy(cell) - goal["xy"]))
        direct = cell == start and segment_free(grid, start_xy, goal["xy"])
        if direct:
            length = float(np.linalg.norm(np.asarray(start_xy) - goal["xy"]))
        reachable.append((length, cell, goal, direct))
    if not reachable:
        return None
    length, node, goal, direct = min(reachable, key=lambda item: item[0])
    cells = [node]
    while node in parent:
        node = parent[node]
        cells.append(node)
    xy = (
        [list(start_xy), goal["xy"]]
        if direct
        else [list(start_xy), *(grid.xy(c).tolist() for c in reversed(cells)), goal["xy"]]
    )
    return {"xy": xy, "length_m": float(length), "goal": goal, "reachable_goals": [item[2] for item in reachable]}


@dataclass
class OccupancyMap:
    origin: np.ndarray
    resolution: float
    occupied: np.ndarray
    obstacle_distance_m: np.ndarray
    support_obstacles: tuple = ()

    def navigation(self, footprint_radius, margin):
        free = ~self.occupied & (self.obstacle_distance_m >= footprint_radius + margin)
        return Grid(self.origin, self.resolution, free, self.support_obstacles)

    def clearance_base(self, chassis_radius):
        """Physical chassis clearance; navigation margin is not subtracted."""
        return self.obstacle_distance_m - chassis_radius


def _mark_box(occupied, xy, lo, hi, resolution):
    """Occupy raster cells intersecting a measured conservative AABB, including obstacles thinner
    than one cell. No robot radius/margin enters here."""
    occupied |= ((xy >= np.asarray(lo)[:2] - resolution / 2) & (xy <= np.asarray(hi)[:2] + resolution / 2)).all(axis=-1)


def scene_occupancy(sim, anchor, radius, resolution, *, support_obstacles=(), robot_height=0.40):
    """Rasterize once without inflation, then measure distance to occupied cells.

    radius=None uses the full source floor. D is conservative at raster precision:
    occupied cells cover their whole square, including thin intersecting geoms.
    """
    triangles = room_triangles(sim)
    if radius is None:
        if sim.local_model and sim.local_model.get("removed_roots"):
            raise ValueError("room navigation requires a full scene")
        if not len(triangles):
            raise ValueError("room navigation requires bounded source floor geometry")
        points = triangles.reshape(-1, 2)
        origin = np.floor(points.min(axis=0) / resolution) * resolution
        nx, ny = np.ceil((points.max(axis=0) - origin) / resolution).astype(int) + 1
    else:
        nx = ny = int(math.ceil(2 * radius / resolution)) + 1
        origin = np.asarray(anchor[:2]) - radius
    yy, xx = np.indices((ny, nx))
    xy = origin + np.stack((xx, yy), axis=-1) * resolution
    allowed = (
        np.ones((ny, nx), dtype=bool)
        if radius is None
        else np.linalg.norm(xy - np.asarray(anchor[:2]), axis=-1) <= radius + 1e-9
    )
    if len(triangles):
        floor = np.zeros((ny, nx), dtype=bool)
        for triangle in triangles:
            u, v = triangle[1] - triangle[0], triangle[2] - triangle[0]
            p = xy - triangle[0]
            det = u[0] * v[1] - u[1] * v[0]
            a = (p[..., 0] * v[1] - p[..., 1] * v[0]) / det
            b = (u[0] * p[..., 1] - u[1] * p[..., 0]) / det
            floor |= (a >= -1e-9) & (b >= -1e-9) & (a + b <= 1 + 1e-9)
        allowed &= floor
    occupied = ~allowed

    for g in range(sim.model.ngeom):
        name = sim.model.geom(g).name
        body = sim.model.body(int(sim.model.geom_bodyid[g])).name
        if body.startswith("robot_0/") or "floor" in name.lower():
            continue
        if not (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g]):
            continue
        if sim.model.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE:
            continue
        points = geom_points(sim, g)
        lo, hi = points.min(axis=0), points.max(axis=0)
        if hi[2] > 0.005 and lo[2] < robot_height:
            _mark_box(occupied, xy, lo, hi, resolution)
    for obstacle in support_obstacles:
        _mark_box(occupied, xy, obstacle["min"], obstacle["max"], resolution)
    distance = distance_transform_edt(np.pad(~occupied, 1, constant_values=False))[1:-1, 1:-1] * resolution
    distance = np.maximum(0.0, distance - resolution / math.sqrt(2))
    return OccupancyMap(origin, resolution, occupied, distance, tuple(support_obstacles))


def navigation_grid(sim, task, settings, footprint_radius, robot_height):
    """Whole-room A* grid around a task; chassis-only navigation clears a fixed chassis height."""
    return scene_grid(
        sim,
        task["anchor_world"],
        None,
        settings.map_resolution_m,
        footprint_radius,
        settings.navigation_margin_m,
        robot_height=robot_height if settings.navigation_whole_body else CHASSIS_HEIGHT_M,
        support_obstacles=task_support_obstacles(sim, task),
    )


def scene_grid(
    sim, anchor, radius, resolution, footprint_radius, margin, *, support_obstacles=(), robot_height=CHASSIS_HEIGHT_M
):
    occupancy = scene_occupancy(
        sim, anchor, radius, resolution, support_obstacles=support_obstacles, robot_height=robot_height
    )
    return occupancy.navigation(footprint_radius, margin)
