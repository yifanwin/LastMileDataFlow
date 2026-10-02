"""Four construction templates: geometry only, never robot success labels."""
import numpy as np
from ..scenes.geometry import body_points, descendants


def range_check(value, bounds):
    return bool(bounds[0] <= value <= bounds[1])


def requirements(sim, config):
    p, m, d = config.parameters, sim.model, sim.data
    target = m.body(config.target).id
    checks = []

    def add(name, passed, evidence):
        checks.append({'requirement': name, 'required': True, 'status': 'pass' if passed else 'fail', 'evidence': evidence})

    if 'distance_range_m' in p:
        distance = float(np.linalg.norm(d.xpos[target,:2] - sim.robot.group('base')[:2]))
        add('target_robot_horizontal_distance', range_check(distance,p['distance_range_m']), {'distance_m':distance,'range_m':p['distance_range_m']})
    if 'height_range_m' in p:
        height = float(d.xpos[target,2])
        add('target_body_origin_height',range_check(height,p['height_range_m']),{'height_m':height,'range_m':p['height_range_m']})
    if config.case_type == 'case2':
        handle = p['handle']; body = m.body(handle['body']).id
        if body not in descendants(m, target): raise ValueError('handle annotation is not a target part')
        actual = d.xmat[body].reshape(3,3) @ np.asarray(handle['axis_local'])
        desired = np.asarray(p['desired_direction_world'],dtype=float)
        angle = float(np.arccos(np.clip(np.dot(actual,desired)/np.linalg.norm(actual)/np.linalg.norm(desired),-1,1)))
        add('verified_handle_world_direction',angle <= p.get('direction_tolerance_rad',.15),
            {'angle_rad':angle,'actual_world':actual.tolist(),'annotation':handle})
    elif config.case_type == 'case3':
        try: obstacle = m.body(p['obstacle']).id
        except KeyError:
            add('obstacle_approach_corridor',False,{'reason':'required_obstacle_missing'})
            return checks
        delta = d.xpos[obstacle]-d.xpos[target]
        expected = np.asarray(p['approach_offset_m'])
        tolerance = p['obstacle_distance_range_m']
        distance = float(np.linalg.norm(delta[:2]))
        # Explicit world-frame approach corridor, rather than inferred reachability.
        add('obstacle_approach_corridor',range_check(distance,tolerance) and np.linalg.norm(delta[:2]-expected[:2]) <= .08,
            {'distance_m':distance,'delta_world_m':delta.tolist(),'expected_world_m':expected.tolist(),'range_m':tolerance})
    elif config.case_type == 'case1.5':
        frame = m.body(p['frame_body']).id
        rot = d.xmat[frame].reshape(3,3)
        side_points = [d.xpos[frame] + rot @ np.asarray(p[key]) for key in ('side_a','side_b')]
        distances = [float(np.linalg.norm(x[:2]-d.xpos[target,:2])) for x in side_points]
        clearances = []
        excluded = descendants(m,frame) | descendants(m,target)
        for point in side_points:
            clearance = p['clearance_radius_m']
            for g in range(m.ngeom):
                b = int(m.geom_bodyid[g])
                if b in excluded or m.body(b).name.startswith('robot_0/') or not (m.geom_contype[g] or m.geom_conaffinity[g]): continue
                if m.geom_type[g] == 0: continue # infinite ground plane
                # Conservative bounding circle clearance, not navigability.
                clearance = min(clearance, max(0., float(np.linalg.norm(d.geom_xpos[g,:2]-point[:2])-m.geom_rbound[g])))
            clearances.append(clearance)
        add('frame_bound_side_distance_difference',distances[1]-distances[0] >= p['min_distance_difference_m'],
            {'frame_body':p['frame_body'],'side_points_world': [x.tolist() for x in side_points], 'distances_m':distances})
        add('frame_bound_side_clearance_difference',clearances[0]-clearances[1] >= p['min_clearance_difference_m'],
            {'clearances_m':clearances,'method':'conservative_collision_bounding_circle_not_navigation'})
    return checks


def pending_hypotheses(case):
    return {'case1':['current_station_failure','alternative_station_success'],
            'case2':['real_handle_grasp_success'], 'case3':['real_planning_path_obstructed','possible_bypass'],
            'case1.5':['real_side_navigation_and_manipulation_difference']}[case]
