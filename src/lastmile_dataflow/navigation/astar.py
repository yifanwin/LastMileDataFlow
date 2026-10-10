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
            return {'xy': np.asarray(xy).tolist(), 'length_m': cost,
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


def scene_grid(sim, anchor, radius, resolution, footprint_radius, margin, *, support_obstacles=()):
    """Conservative PER-GEOM chassis-height raster; physical whole-body checks follow."""
    import mujoco
    from ..scenes.geometry import geom_points
    from ..scenes.initialization import room_triangles,inside_triangles
    size = int(math.ceil(2*radius/resolution))+1
    origin = np.asarray(anchor[:2])-radius
    yy,xx = np.indices((size,size)); xy = origin+np.stack((xx,yy),axis=-1)*resolution
    free = np.linalg.norm(xy-np.asarray(anchor[:2]),axis=-1) <= radius+1e-9
    triangles = room_triangles(sim)
    if len(triangles):
        for y,x in np.argwhere(free):
            free[y,x] = inside_triangles(xy[y,x],triangles)
    inflation = footprint_radius+margin
    for g in range(sim.model.ngeom):
        name = sim.model.geom(g).name; body = sim.model.body(int(sim.model.geom_bodyid[g])).name
        if body.startswith('robot_0/') or 'floor' in name.lower():
            continue
        if not (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g]):
            continue
        if sim.model.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE:
            continue
        points = geom_points(sim,g); lo,hi = points.min(axis=0),points.max(axis=0)
        if hi[2] <= .005 or lo[2] >= .40:
            continue
        dx = np.maximum(np.maximum(lo[0]-xy[:,:,0],xy[:,:,0]-hi[0]),0.)
        dy = np.maximum(np.maximum(lo[1]-xy[:,:,1],xy[:,:,1]-hi[1]),0.)
        free[dx**2+dy**2 <= inflation**2] = False
    # Reserve the entire support footprint, including elevated tabletops whose
    # legs alone would otherwise leave an apparently navigable interior.
    for obstacle in support_obstacles:
        lo,hi = np.asarray(obstacle['min']),np.asarray(obstacle['max'])
        dx = np.maximum(np.maximum(lo[0]-xy[:,:,0],xy[:,:,0]-hi[0]),0.)
        dy = np.maximum(np.maximum(lo[1]-xy[:,:,1],xy[:,:,1]-hi[1]),0.)
        free[dx**2+dy**2 <= inflation**2] = False
    return Grid(origin,resolution,free,tuple(support_obstacles))


def task_support_obstacles(sim, task):
    """Conservative support envelopes from actual geometry, not success labels.

    Parent metadata is a hint only: require an overlapping surface immediately
    below the target. Without a hint, search collision surfaces under the target.
    Open tasks reserve their articulated furniture instance instead.
    """
    from ..scenes.geometry import body_points, collision_geoms, descendants, geom_points
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
