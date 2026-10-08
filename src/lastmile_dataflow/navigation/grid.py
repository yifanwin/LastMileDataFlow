"""Deterministic conservative occupancy and 4-connected A*, no diagonal corner cuts.

A grid path is a construction prerequisite, NOT evidence of navigation execution.
"""
import heapq
import math
import numpy as np


def inflate_obstacles(grid, radius_cells):
    grid = np.asarray(grid, dtype=bool)
    if grid.ndim != 2 or type(radius_cells) is not int or radius_cells < 0:
        raise ValueError('2-D occupancy and nonnegative integer radius required')
    # Out-of-map space is occupied. A disk conservatively covers the base rotation envelope.
    padded = np.pad(grid, radius_cells, constant_values=True)
    result = np.zeros_like(grid)
    r = radius_cells
    for dx in range(-r, r+1):
        for dy in range(-r, r+1):
            if dx*dx+dy*dy <= r*r:
                result |= padded[r+dx:r+dx+grid.shape[0], r+dy:r+dy+grid.shape[1]]
    return result


def find_path(grid, start, goal, *, max_expansions=None):
    grid = np.asarray(grid, dtype=bool)
    if grid.ndim != 2:
        raise ValueError('2-D occupancy required')
    start, goal = tuple(start), tuple(goal)
    def valid(p):
        return len(p) == 2 and all(type(x) is int for x in p) and all(0 <= p[i] < grid.shape[i] for i in (0, 1)) and not grid[p]
    if not valid(start) or not valid(goal): return None
    if max_expansions is None: max_expansions = grid.size
    if type(max_expansions) is not int or max_expansions <= 0: raise ValueError('invalid search budget')
    def h(p): return abs(p[0]-goal[0])+abs(p[1]-goal[1])
    queue = [(h(start), 0, start)]; cost = {start: 0}; parent = {}; expanded = 0
    while queue and expanded < max_expansions:
        _, g, p = heapq.heappop(queue)
        if g != cost[p]: continue
        expanded += 1
        if p == goal:
            path = [p]
            while p in parent: p = parent[p]; path.append(p)
            return path[::-1]
        for dx, dy in ((-1, 0), (0, -1), (0, 1), (1, 0)):
            q = (p[0]+dx, p[1]+dy)
            if valid(q) and g+1 < cost.get(q, math.inf):
                cost[q] = g+1; parent[q] = p; heapq.heappush(queue, (g+1+h(q), g+1, q))
    return None


def scene_grid(sim, *, radius_m, resolution_m=.1, bounds=None, max_cells=100000):
    """Conservative collision envelopes + measured floor mask, NOT exact meshes."""
    from ..scenes.geometry import geom_points
    from ..scenes.initialization import room_triangles, inside_triangles, floor_support
    import mujoco
    triangles=room_triangles(sim)
    if bounds is None:
        if not len(triangles): raise ValueError('explicit finite bounds required without room floor mesh')
        points=triangles.reshape(-1,2); bounds=[points.min(axis=0).tolist(),points.max(axis=0).tolist()]
    lo,hi=np.asarray(bounds,float)
    if resolution_m<=0 or radius_m<=0 or not np.isfinite([*lo,*hi,resolution_m,radius_m]).all() or np.any(hi<=lo): raise ValueError('invalid map dimensions')
    shape=np.ceil((hi-lo)/resolution_m).astype(int)+1
    if np.prod(shape)>max_cells: raise ValueError('occupancy map exceeds budget')
    occupied=np.ones(tuple(shape),bool)
    for x,y in np.ndindex(occupied.shape):
        xy=lo+resolution_m*np.array([x,y])
        occupied[x,y]=not inside_triangles(xy,triangles) if len(triangles) else not floor_support(sim,xy)
    for g in range(sim.model.ngeom):
        name=sim.model.geom(g).name.lower(); body=sim.model.body(int(sim.model.geom_bodyid[g])).name
        if body.startswith('robot_0/') or 'floor' in name or name.startswith('room_') or not (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g]): continue
        try: p=geom_points(sim,g)
        except ValueError as exc: raise ValueError('unsupported occupancy collision geometry') from exc
        if p[:,2].max()<.03 or p[:,2].min()>.6: continue
        a=np.maximum(0,np.floor((p[:,:2].min(axis=0)-lo)/resolution_m).astype(int))
        b=np.minimum(shape-1,np.ceil((p[:,:2].max(axis=0)-lo)/resolution_m).astype(int))
        occupied[a[0]:b[0]+1,a[1]:b[1]+1]=True
    inflated=inflate_obstacles(occupied,int(np.ceil((radius_m+resolution_m/np.sqrt(2))/resolution_m)))
    return {'occupied':inflated,'origin':lo,'resolution_m':resolution_m,
            'method':'floor_mask_and_conservative_low_collision_AABB_disk_inflation',
            'navigation_execution':'unknown'}


def ground_path(map_data,start,goal):
    origin=np.asarray(map_data['origin']); resolution=map_data['resolution_m']
    # Add endpoints as checked short connectors to cell centres; no blind nearest-free snapping.
    cells=[tuple(int(v) for v in np.rint((np.array(p[:2])-origin)/resolution)) for p in (start,goal)]
    path=find_path(map_data['occupied'],*cells)
    poses=([list(start[:2])]+[(origin+resolution*np.array(c)).tolist() for c in path]+[list(goal[:2])]) if path else []
    return {'status':'pass' if path else 'unknown','poses':poses,
            'method':map_data['method'],'resolution_m':resolution,'navigation_execution':'unknown'}
