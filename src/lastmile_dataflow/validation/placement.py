"""Build-only support, stability, collision and side-effect acceptance."""
import mujoco
import numpy as np
from ..construction.cases import requirement_summary, requirements
from ..scenes.geometry import body_points, collision_geoms, quat_angle, support_rays, extract_regions


def poses(sim):
    return {x['instance_id']: np.r_[sim.data.xpos[x['body_id']],sim.data.xquat[x['body_id']]].copy()
            for x in sim.catalog}


def placement_check(sim, name, region, protocol):
    body = sim.model.body(name).id
    points = body_points(sim,body)
    local = region.local(points)
    a,b,c,d = region.bounds; margin = protocol.edge_margin_m
    footprint = bool(local[:,0].min() >= a+margin and local[:,0].max() <= b-margin and local[:,1].min() >= c+margin and local[:,1].max() <= d-margin)
    bottom = float(points[:,2].min())
    height = abs(bottom-region.height) <= protocol.support_tolerance_m
    correct_surface = support_rays(sim, body, region, points)
    object_geoms = set(collision_geoms(sim,body)); support_geom = sim.model.geom(region.geom).id
    contacts = [c for c in sim.data.contact if (int(c.geom1) in object_geoms and int(c.geom2) == support_geom)
                or (int(c.geom2) in object_geoms and int(c.geom1) == support_geom)]
    supported = bool(contacts and any(c.dist <= protocol.support_tolerance_m for c in contacts))
    return {'object':name,'region_id':region.region_id,'valid':bool(footprint and height and correct_surface and supported),
            'footprint_inside':footprint,'height_correct':height,'support_surface_covered':correct_surface,
            'designated_support_contact':supported,'bottom_height_m':bottom,'support_height_m':region.height}


def inspect_build(session, history, baseline, *, include_requirements=True):
    sim, config = session.sim, session.config
    m,d,p = sim.model, sim.data, config.protocol
    issues, placement = [], []
    if not all(np.isfinite(x).all() for x in (d.qpos,d.qvel,d.qacc,d.ctrl)):
        return {'valid':False,'scene_valid':False,'issues':[{'code':'nonfinite_state'}],'requirements':[]}
    for h in history:
        issues.extend(h.get('anomalies',[]))
    for i,w in enumerate(d.warning):
        if w.number: issues.append({'code':'mujoco_warning','id':i,'count':int(w.number)})
    for c in d.contact:
        if c.dist < -p.penetration_m and m.geom_bodyid[c.geom1] != m.geom_bodyid[c.geom2]:
            issues.append({'code':'severe_penetration','distance_m':float(c.dist),'geoms':[m.geom(int(c.geom1)).name,m.geom(int(c.geom2)).name]})
    for name, region in session.placements.items():
        # Re-extract the region so structural support movement cannot leave stale geometry.
        fresh = next((r for r in extract_regions(sim,region.support,region.geom) if r.region_id == region.region_id),None)
        if fresh is None:
            issues.append({'code':'support_region_missing','object':name}); continue
        check = placement_check(sim,name,fresh,p); placement.append(check)
        if not check['valid']: issues.append({'code':'wrong_support_or_region','object':name})
    tail = [h for h in history if h['time'] >= d.time-p.window_s]
    stability = []
    for name in session.placements:
        samples = [h['objects'][name] for h in tail if name in h['objects']]
        all_samples = [h['objects'][name] for h in history if name in h['objects']]
        if len(samples) < 2 or tail[-1]['time']-tail[0]['time'] < p.window_s-m.opt.timestep*1.1:
            issues.append({'code':'stability_window_missing','object':name}); continue
        velocity = max(np.linalg.norm(s['velocity'][3:]) for s in samples)
        angular = max(np.linalg.norm(s['velocity'][:3]) for s in samples)
        drift = max(np.linalg.norm(s['pose'][:3]-samples[0]['pose'][:3]) for s in samples)
        rotation = max(quat_angle(s['pose'][3:],samples[0]['pose'][3:]) for s in samples)
        flight = max(np.linalg.norm(s['pose'][:3]-all_samples[0]['pose'][:3]) for s in all_samples)
        ok = velocity <= p.speed_m_s and angular <= p.angular_speed_rad_s and drift <= p.drift_m and rotation <= p.rotation_rad and flight <= p.flight_m
        stability.append({'object':name,'valid':bool(ok),'max_speed_m_s':float(velocity),'max_angular_rad_s':float(angular),
                          'window_drift_m':float(drift),'window_rotation_rad':float(rotation),'trajectory_excursion_m':float(flight)})
        if not ok: issues.append({'code':'unstable_or_flight','object':name})
    current = poses(sim)
    for name, before in baseline.items():
        if name not in current:
            issues.append({'code':'protected_missing','object':name}); continue
        after = current[name]
        shift = float(np.linalg.norm(after[:3]-before[:3])); angle = quat_angle(after[3:],before[3:])
        if shift > p.protected_translation_m or angle > p.protected_rotation_rad:
            issues.append({'code':'protected_or_neighbor_changed','object':name,'translation_m':shift,'rotation_rad':angle})
    robot_delta = np.abs(sim.robot.group('base')-session.initial_robot_base)
    if np.linalg.norm(robot_delta[:2]) > p.robot_translation_m or robot_delta[2] > p.robot_rotation_rad:
        issues.append({'code':'robot_initial_state_drift','delta':robot_delta.tolist()})
    # Keep phase-one initialization's real room/floor check, and enforce joint limits.
    if session.initialization['status'] != 'valid': issues.append({'code':'invalid_robot_initialization'})
    for name,j in sim.robot.joints.items():
        q = d.qpos[m.jnt_qposadr[j]]
        if m.jnt_limited[j] and not m.jnt_range[j,0]-.01 <= q <= m.jnt_range[j,1]+.01:
            issues.append({'code':'robot_joint_limit','joint':name,'q':float(q)})
    req = requirements(session) if include_requirements else []
    # Scene validity counts physical evidence only. A failing case_intent requirement is what the
    # build loop exists to fix; it must never be reported as an invalid scene.
    summary = requirement_summary(req)
    return {'valid': not issues and summary['status'] == 'pass', 'scene_valid': not issues,
            'issues': issues, 'placements': placement, 'stability': stability, 'requirements': req,
            'requirement_summary': summary, 'protocol': p.version}
