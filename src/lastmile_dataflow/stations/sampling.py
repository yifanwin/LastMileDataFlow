"""Deterministic coarse samples, physical floor/collision filter and local refinement."""
import numpy as np
import mujoco
from ..scenes.initialization import inside_triangles, room_triangles, floor_support
from ..validation.lightweight import inspect_state


def coarse_samples(config, target_xyz, source_base):
    poses=[]
    def add(base,level):
        base=np.asarray(base,float).copy(); base[2]=np.arctan2(np.sin(base[2]),np.cos(base[2]))
        if any(np.max(np.abs(base-np.array(p['base']))) < 1e-6 for p in poses): return
        poses.append({'station_id':f'S{len(poses):03d}','base':base.tolist(),'level':level})
    # Explicit probes first: they are input initializations, never navigation evidence.
    for pose in config.probes: add(pose,'probe')
    if config.include_source_base: add(source_base,'source')
    for radius in config.radii_m:
        for a in np.arange(0,360,config.angle_step_deg):
            xy=np.array(target_xyz[:2])+radius*np.array([np.cos(np.deg2rad(a)),np.sin(np.deg2rad(a))])
            yaw=np.arctan2(target_xyz[1]-xy[1],target_xyz[0]-xy[0])
            for offset in config.yaw_offsets_deg: add([*xy,yaw+np.deg2rad(offset)],'coarse')
    return poses[:config.max_candidates]


def place_base(sim, base):
    if sim.started or sim.closed: raise RuntimeError('base placement only before independent execution')
    before=sim.data.qpos.copy()
    addresses=[sim.robot.addresses[n] for n in sim.robot.groups['base']]
    for name,q in zip(sim.robot.groups['base'],base): sim.robot._check_joint(name,q)
    sim.data.qpos[addresses]=base
    sim.robot.hold(); mujoco.mj_forward(sim.model,sim.data)
    mask=np.ones(sim.model.nq,bool); mask[addresses]=False
    if not np.array_equal(before[mask],sim.data.qpos[mask]): raise RuntimeError('station changed non-base state')
    return {'method':'independent_base_initialization_not_navigation','non_base_qpos_unchanged':True,
            'base':sim.robot.group('base').tolist()}


def filter_station(sim,collection):
    base=sim.robot.group('base'); ground=floor_support(sim,base[:2])
    check=inspect_state(sim,collection)
    collisions=[]
    for c in sim.data.contact:
        a,b=[sim.model.body(int(sim.model.geom_bodyid[g])).name for g in (c.geom1,c.geom2)]
        ga,gb=[sim.model.geom(int(g)).name for g in (c.geom1,c.geom2)]
        if c.dist < -.001 and (a.startswith('robot_0/') or b.startswith('robot_0/')):
            floor_base=('floor' in (ga+gb).lower() and any('base' in n or 'wheel' in n for n in (a,b) if n.startswith('robot_0/')))
            if not floor_base: collisions.append({'bodies':[a,b],'distance_m':float(c.dist)})
    valid=bool(ground and inside_triangles(base[:2],room_triangles(sim)) and check['valid'] and not collisions)
    return {'status':'valid' if valid else 'geometry_filtered','ground':ground,'robot_collisions':collisions,
            'severe_issues':[v for v in check['issues'] if v['severity']=='error']}


def refinements(config, rows, all_samples):
    if config.max_refinements==0: return []
    # Only refine near a measured success and a nearby conflicting observation.
    successful=[r for r in rows if r.get('execution')=='success']
    difficult=[r for r in rows if r.get('execution')=='failure' or r.get('planning')=='no_solution']
    result=[]; seen=[np.asarray(r['base']) for r in all_samples]
    for a in successful:
        near=[b for b in difficult if 1e-6 < np.linalg.norm(np.array(a['base'][:2])-b['base'][:2]) < .7
              and all(a.get(k)==b.get(k) for k in ('arm','torso_h','grasp_row','approach_offset_m'))]
        if not near: continue
        for dx,dy in ((config.refine_step_m,0),(-config.refine_step_m,0),(0,config.refine_step_m),(0,-config.refine_step_m)):
            base=np.array(a['base'])+np.array([dx,dy,0])
            if any(np.max(np.abs(base-p)) < 1e-6 for p in seen): continue
            result.append({'station_id':f'S{len(all_samples)+len(result):03d}','base':base.tolist(),'level':'refined','parent':a['station_id']})
            seen.append(base)
            if len(result)>=config.max_refinements: return result
    return result


def edge_samples(region, target_xyz, *, base_radius_m, edge_gap_m=.1, spacing_m=.2, yaw_offsets_rad=(0.,)):
    """Support-local edges, not world north/south. Samples still need physical filtering."""
    if any(type(v) not in (float, int) or not np.isfinite(v) or v <= 0 for v in (base_radius_m, spacing_m)) or not np.isfinite(edge_gap_m) or edge_gap_m < 0:
        raise ValueError('invalid edge sampling dimensions')
    if not yaw_offsets_rad or any(not np.isfinite(v) for v in yaw_offsets_rad): raise ValueError('invalid yaw samples')
    a,b,c,d=region.bounds; result=[]; axes=np.asarray(region.axes); origin=np.asarray(region.origin)
    for edge,lo,hi,fixed,axis,sign in (('u-',c,d,a,0,-1),('u+',c,d,b,0,1),('v-',a,b,c,1,-1),('v+',a,b,d,1,1)):
        positions=np.linspace(lo,hi,max(2,int(np.ceil((hi-lo)/spacing_m))+1))
        for v in positions:
            xy=np.array([fixed,v] if axis==0 else [v,fixed],float)
            xy[axis]+=sign*(base_radius_m+edge_gap_m)
            world=origin+axes@np.r_[xy,0.]
            yaw=np.arctan2(target_xyz[1]-world[1],target_xyz[0]-world[0])
            for offset in yaw_offsets_rad:
                angle=float(np.arctan2(np.sin(yaw+offset),np.cos(yaw+offset)))
                result.append({'station_id':f'S{len(result):04d}','edge':edge,'base':[float(world[0]),float(world[1]),angle],
                               'edge_gap_m':float(edge_gap_m),'level':'edge'})
    return result
