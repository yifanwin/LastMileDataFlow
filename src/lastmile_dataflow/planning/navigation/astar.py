"""Eight-neighbor A* with no corner cutting and metric distance fields."""
from dataclasses import dataclass
import heapq
import math
import numpy as np


@dataclass
class Grid:
    origin: np.ndarray
    resolution: float
    free: np.ndarray
    support_obstacles: tuple = ()

    def cell(self, xy):
        x,y = np.rint((np.asarray(xy)-self.origin)/self.resolution).astype(int)
        if 0 <= y < self.free.shape[0] and 0 <= x < self.free.shape[1]:
            return (int(y),int(x))
        return None

    def xy(self, cell):
        y,x = cell
        return self.origin+np.array([x,y])*self.resolution

    def neighbors(self, cell):
        y,x = cell
        for dy,dx in ((0,1),(1,0),(0,-1),(-1,0),(1,1),(1,-1),(-1,1),(-1,-1)):
            yy,xx = y+dy,x+dx
            if not (0 <= yy < self.free.shape[0] and 0 <= xx < self.free.shape[1]) or not self.free[yy,xx]:
                continue
            if dy and dx and (not self.free[y,xx] or not self.free[yy,x]):
                continue
            yield (yy,xx), self.resolution*(math.sqrt(2) if dx and dy else 1.)


def search(grid, start_xy, goal_xy):
    start,goal = grid.cell(start_xy),grid.cell(goal_xy)
    if start is None or goal is None or not grid.free[start] or not grid.free[goal]:
        return None
    if not segment_free(grid, start_xy, grid.xy(start)) or not segment_free(grid, grid.xy(goal), goal_xy):
        return None
    distance = {start: 0.}; parent = {}; queue = [(0.,0.,start)]
    while queue:
        _,cost,node = heapq.heappop(queue)
        if cost > distance[node]+1e-10:
            continue
        if node == goal:
            cells = [node]
            while node in parent:
                node = parent[node]; cells.append(node)
            xy = [np.asarray(start_xy), *(grid.xy(c) for c in reversed(cells)), np.asarray(goal_xy)]
            length = float(np.linalg.norm(np.diff(xy, axis=0), axis=1).sum())
            return {'xy': np.asarray(xy).tolist(), 'length_m': length,
                    'cells': [list(c) for c in reversed(cells)]}
        for neighbor,step in grid.neighbors(node):
            value = cost+step
            if value+1e-10 < distance.get(neighbor,float('inf')):
                distance[neighbor] = value; parent[neighbor] = node
                heuristic = np.linalg.norm(np.asarray(neighbor)-goal)*grid.resolution
                heapq.heappush(queue,(value+heuristic,value,neighbor))
    return None


def distance_field(grid, source_xy, limit):
    distances = np.full(grid.free.shape,np.inf)
    start = grid.cell(source_xy)
    if start is None or not grid.free[start]:
        return distances
    distances[start] = 0.; queue = [(0.,start)]
    while queue:
        cost,node = heapq.heappop(queue)
        if cost > distances[node]+1e-10:
            continue
        for neighbor,step in grid.neighbors(node):
            value = cost+step
            if value <= limit and value+1e-10 < distances[neighbor]:
                distances[neighbor] = value; heapq.heappush(queue,(value,neighbor))
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
    distance = {start: 0.}
    parent = {}
    queue = [(0., start)]
    while queue:
        cost, node = heapq.heappop(queue)
        if cost > distance[node] + 1e-10:
            continue
        for neighbor, step in grid.neighbors(node):
            value = cost + step
            if value + 1e-10 < distance.get(neighbor, float('inf')):
                distance[neighbor] = value
                parent[neighbor] = node
                heapq.heappush(queue, (value, neighbor))
    reachable = []
    start_offset = float(np.linalg.norm(np.asarray(start_xy) - grid.xy(start)))
    for goal in goals:
        cell = grid.cell(goal['xy'])
        if cell not in distance or not segment_free(grid, grid.xy(cell), goal['xy']):
            continue
        length = start_offset + distance[cell] + float(np.linalg.norm(grid.xy(cell) - goal['xy']))
        direct = cell == start and segment_free(grid, start_xy, goal['xy'])
        if direct:
            length = float(np.linalg.norm(np.asarray(start_xy) - goal['xy']))
        reachable.append((length, cell, goal, direct))
    if not reachable:
        return None
    length, node, goal, direct = min(reachable, key=lambda item: item[0])
    cells = [node]
    while node in parent:
        node = parent[node]
        cells.append(node)
    xy = [list(start_xy), goal['xy']] if direct else [
        list(start_xy), *(grid.xy(c).tolist() for c in reversed(cells)), goal['xy']]
    return {'xy': xy, 'length_m': float(length), 'goal': goal,
            'reachable_goals': [item[2] for item in reachable]}


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


def scene_occupancy(sim, anchor, radius, resolution, *, support_obstacles=(), robot_height=.40):
    """Rasterize once without inflation, then measure distance to occupied cells.

    radius=None uses the full source floor. D is conservative at raster precision:
    occupied cells cover their whole square, including thin intersecting geoms.
    """
    import mujoco
    from scipy.ndimage import distance_transform_edt
    from ...scenes.geometry import geom_points
    from ...scenes.stations.initialization import room_triangles
    triangles = room_triangles(sim)
    if radius is None:
        if getattr(sim, 'local_model', {}).get('removed_roots'):
            raise ValueError('room navigation requires a full scene; rebuild with local_task_models=false')
        if not len(triangles):
            raise ValueError('room navigation requires bounded source floor geometry')
        points = triangles.reshape(-1, 2)
        origin = np.floor(points.min(axis=0) / resolution) * resolution
        nx, ny = np.ceil((points.max(axis=0) - origin) / resolution).astype(int) + 1
    else:
        nx = ny = int(math.ceil(2 * radius / resolution)) + 1
        origin = np.asarray(anchor[:2]) - radius
    yy, xx = np.indices((ny, nx))
    xy = origin + np.stack((xx, yy), axis=-1) * resolution
    allowed = np.ones((ny, nx), dtype=bool) if radius is None else np.linalg.norm(xy - np.asarray(anchor[:2]), axis=-1) <= radius + 1e-9
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

    def occupy(lo, hi):
        # Raster cells intersecting the measured conservative AABB, including
        # obstacles thinner than one cell. No robot radius/margin enters here.
        occupied[:] |= ((xy >= np.asarray(lo)[:2] - resolution / 2) &
                        (xy <= np.asarray(hi)[:2] + resolution / 2)).all(axis=-1)

    for g in range(sim.model.ngeom):
        name = sim.model.geom(g).name
        body = sim.model.body(int(sim.model.geom_bodyid[g])).name
        if body.startswith('robot_0/') or 'floor' in name.lower():
            continue
        if not (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g]):
            continue
        if sim.model.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE:
            continue
        points = geom_points(sim, g)
        lo, hi = points.min(axis=0), points.max(axis=0)
        if hi[2] > .005 and lo[2] < robot_height:
            occupy(lo, hi)
    for obstacle in support_obstacles:
        occupy(obstacle['min'], obstacle['max'])
    distance = distance_transform_edt(np.pad(~occupied, 1, constant_values=False))[1:-1, 1:-1] * resolution
    distance = np.maximum(0., distance - resolution / math.sqrt(2))
    return OccupancyMap(origin, resolution, occupied, distance, tuple(support_obstacles))


def scene_grid(sim, anchor, radius, resolution, footprint_radius, margin, *, support_obstacles=(), robot_height=.40):
    occupancy = scene_occupancy(sim, anchor, radius, resolution,
                                support_obstacles=support_obstacles, robot_height=robot_height)
    return occupancy.navigation(footprint_radius, margin)


def task_support_obstacles(sim, task):
    """Conservative support envelopes from actual geometry, not success labels.

    Parent metadata is a hint only: require an overlapping surface immediately
    below the target. Without a hint, search collision surfaces under the target.
    Open tasks reserve their articulated furniture instance instead.
    """
    from ...scenes.geometry import body_points, collision_geoms, descendants, geom_points
    target = sim.model.body(task['target_body']).id
    target_bodies = descendants(sim.model,target)
    target_lo = body_points(sim,target).min(axis=0)
    anchor = np.asarray(task['anchor_world'])
    hint = next((e.get('parent_instance_id') for e in sim.catalog
                 if e['instance_id'] == task['instance_id']),None)
    candidates = []
    for entry in sim.catalog:
        is_open = task['operation'] == 'open' and entry['instance_id'] == task['instance_id']
        if (entry['body_id'] in target_bodies and not is_open) or entry['mjcf_body'].startswith('robot_0/'):
            continue
        points = [geom_points(sim,g) for g in collision_geoms(sim,entry['body_id'])
                  if is_open or int(sim.model.geom_bodyid[g]) not in target_bodies]
        if not points:
            continue
        tops = []
        for pts in points:
            lo,hi = pts.min(axis=0),pts.max(axis=0)
            if np.all(anchor[:2] >= lo[:2]) and np.all(anchor[:2] <= hi[:2]) and abs(hi[2]-target_lo[2]) <= .05:
                tops.append(hi[2])
        if not tops and not is_open:
            continue
        pts = np.concatenate(points); lo,hi = pts.min(axis=0),pts.max(axis=0)
        obstacle = {'body':entry['mjcf_body'],'min':lo.tolist(),'max':hi.tolist(),
                    'kind':'support','evidence':'open_task_furniture' if is_open else 'surface_below_target_AABB'}
        candidates.append((is_open or entry['instance_id'] == hint,max(tops,default=hi[2]),obstacle))
    if not candidates:
        return []
    return [max(candidates,key=lambda item:item[:2])[2]]
