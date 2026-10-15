"""Full-height collision, physical support and matched-control effect checks."""
import mujoco
import numpy as np

from ..geometry import body_points, collision_geoms, extract_regions, support_rays, quat_angle
from .regions import ground_coverage
from .sampling import substantial_asset


def case_construction_check(sim, placements, config):
    """Check requested construction, not grasp difficulty or task completion."""
    policy = config.case_policy
    if policy is None:
        return {'valid': True, 'case': 'legacy', 'scope': 'legacy_no_case_claim'}
    count = len(placements)
    reasons = []
    if policy.rule_version in ('scene-cases-v2', 'scene-cases-v3'):
        from ...planning.astar import task_support_obstacles
        points = body_points(sim, sim.target_id)
        target = sim.model.body(sim.target_id).name
        anchor = ((points.min(axis=0) + points.max(axis=0)) / 2).tolist()
        expected_support = {s['body'] for s in task_support_obstacles(sim,
            {'instance_id': config.target_instance, 'target_body': target, 'operation': 'pick', 'anchor_world': anchor})}
        if not expected_support or any(p['support_instance'] not in expected_support for p in placements if p['kind'] == 'surface'):
            reasons.append('added_objects_not_on_actual_target_support')
    capacity_shortfall = count < config.obstacle_count[0]
    if count > config.obstacle_count[1] or capacity_shortfall:
        reasons.append('object_count_outside_requested_range')
    substantial_count = 0
    for i, placement in enumerate(placements):
        body = sim.model.body(f'procedural_obstacle_{i}').id
        local = (body_points(sim, body) - sim.data.xpos[body]) @ sim.data.xmat[body].reshape(3, 3)
        size = dict(zip(('width', 'depth', 'height'), np.ptp(local, axis=0)))
        substantial_count += substantial_asset({'category': placement['category'], 'size': size}, policy)
    if policy.case == 'Case1':
        chairs = [p for p in placements if p['category'] == 'Chair']
        if len(chairs) != 1 or any(p['kind'] != ('ground' if p['category'] == 'Chair' else 'surface') for p in placements):
            reasons.append('requires_one_ground_chair_and_surface_remainder')
        if policy.rule_version in ('scene-cases-v2', 'scene-cases-v3'):
            surface_count = sum(p['kind'] == 'surface' for p in placements)
            if surface_count > 8 or surface_count < 5:
                reasons.append('requires_5_to_8_added_surface_objects')
            if len({p['support_instance'] for p in placements if p['kind'] == 'surface'}) != 1:
                reasons.append('requires_single_target_support_surface')
        if policy.rule_version == 'scene-cases-v3':
            from ..tasks.eligibility import allowed_case1_support
            from .regions import support_regions
            supports = support_regions(sim, {'instance_id': config.target_instance, 'target_body': target,
                'operation': 'pick', 'anchor_world': anchor, 'support_obstacles': task_support_obstacles(sim,
                {'instance_id': config.target_instance, 'target_body': target, 'operation': 'pick', 'anchor_world': anchor})})
            if not supports or not allowed_case1_support(sim, supports[0].support, policy.case1_support_types):
                reasons.append('case1_support_not_countertop_or_table')
        anchor = sim.data.xpos[sim.target_id, :2]
        if any(np.linalg.norm(np.asarray(p['point_xy']) - anchor) > policy.near_target_m + .01 for p in placements if p['kind'] == 'surface'):
            reasons.append('surface_object_not_near_target')
    elif policy.case == 'Case2':
        if any(p['kind'] != 'surface' for p in placements) or len({p['support_instance'] for p in placements}) != 1:
            reasons.append('requires_single_target_support_surface')
        if substantial_count < policy.min_substantial:
            reasons.append('not_enough_measured_large_or_tall_objects')
    return {'valid': not reasons, 'case': policy.case, 'rule_version': policy.rule_version, 'object_count': count,
            'added_ground_count': sum(p['kind'] == 'ground' for p in placements),
            'added_surface_count': sum(p['kind'] == 'surface' for p in placements),
            'substantial_count': int(substantial_count), 'failure_reasons': reasons,
            'capacity_shortfall': capacity_shortfall,
            'scope': 'requested_obstacle_construction_not_task_difficulty'}


def collision_pairs(sim, placements, config, *, settled=False):
    """Narrow phase against EACH geom; never exempt a whole table or all world geoms."""
    m, d = sim.model, sim.data
    added = {}
    owner = {}
    for i, placement in enumerate(placements):
        name = f'procedural_obstacle_{i}'
        added[name] = collision_geoms(sim, m.body(name).id)
        for g in added[name]:
            owner[g] = i
    bad, supports, seen = [], [], set()
    contact_distances = {}
    for contact in d.contact:
        key = tuple(sorted((int(contact.geom1), int(contact.geom2))))
        contact_distances[key] = min(contact_distances.get(key, 0.), float(contact.dist))
    for name, geoms in added.items():
        for g in geoms:
            # Plane has zero rbound: include it explicitly, not by sphere broad phase.
            distances = np.linalg.norm(d.geom_xpos - d.geom_xpos[g], axis=1)
            nearby = np.flatnonzero((distances <= m.geom_rbound + m.geom_rbound[g] + .02)
                                    | (m.geom_type == mujoco.mjtGeom.mjGEOM_PLANE))
            for h in nearby:
                h = int(h)
                key = tuple(sorted((g, h)))
                if h == g or key in seen or owner.get(h) == owner[g]:
                    continue
                seen.add(key)
                if not (m.geom_contype[g] & m.geom_conaffinity[h] or m.geom_contype[h] & m.geom_conaffinity[g]):
                    continue
                placement = placements[owner[g]]
                support = ('floor' in m.geom(h).name.lower() and not m.body(int(m.geom_bodyid[h])).name.startswith('robot_0/')) if placement['kind'] == 'ground' else m.geom(h).name == placement['support_geom']
                threshold = config.support_contact_tolerance_m if support else config.collision_tolerance_m
                distance = float(mujoco.mj_geomDistance(m, d, g, h, .02, None))
                # Native geomDistance can return zero for exactly coincident boxes.
                # Actual mj_forward contacts still carry the penetration depth.
                # Use both; zero distance alone is not an overlap certificate.
                distance = min(distance, contact_distances.get(key, distance))
                row = {'obstacle': name, 'geom_ids': [g, h],
                       'geoms': [m.geom(g).name, m.geom(h).name],
                       'bodies': [m.body(int(m.geom_bodyid[g])).name, m.body(int(m.geom_bodyid[h])).name],
                       'distance_m': distance, 'support_pair': bool(support)}
                if distance < -threshold:
                    bad.append(row)
                elif support and distance <= config.support_contact_tolerance_m:
                    supports.append(row)
    return {'valid': not bad, 'illegal_pairs': bad, 'support_pairs': supports,
            'scope': 'all_added_collision_geoms_against_full_height_scene_geoms'}


def load_support(sim, placements, config):
    m, d = sim.model, sim.data
    rows = []
    for i, placement in enumerate(placements):
        name = f'procedural_obstacle_{i}'
        body = m.body(name).id
        geoms = set(collision_geoms(sim, body))
        contacts = []
        for index, c in enumerate(d.contact):
            if c.geom1 not in geoms and c.geom2 not in geoms:
                continue
            other = int(c.geom2 if c.geom1 in geoms else c.geom1)
            correct_support = ('floor' in m.geom(other).name.lower() and not m.body(int(m.geom_bodyid[other])).name.startswith('robot_0/')) if placement['kind'] == 'ground' else m.geom(other).name == placement['support_geom']
            if not correct_support:
                continue
            force = np.zeros(6)
            mujoco.mj_contactForce(m, d, index, force)
            contacts.append({'other_geom': m.geom(other).name, 'distance_m': float(c.dist),
                             'normal_vertical': abs(float(c.frame[2])), 'normal_force_n': float(force[0])})
        points = body_points(sim, body)
        height_error = abs(float(points[:, 2].min()) - placement['support_height_m'])
        tilt = float(np.arccos(np.clip(d.xmat[body].reshape(3, 3)[2, 2], -1, 1)))
        load = any(-config.support_contact_tolerance_m <= c['distance_m'] <= config.support_contact_tolerance_m
                   and c['normal_vertical'] > .7 and c['normal_force_n'] > 1e-8 for c in contacts)
        coverage = ground_coverage(sim, points) if placement['kind'] == 'ground' else False
        if placement['kind'] == 'surface':
            # Recheck the SETTLED footprint. A pre-placement flag is not support proof.
            regions = extract_regions(sim, placement['support_instance'], placement['support_geom'])
            for region in regions:
                local = region.local(points)
                a, b, c, dd = region.bounds
                eps = config.collision_tolerance_m
                inside = bool(local[:, 0].min() >= a - eps and local[:, 0].max() <= b + eps
                              and local[:, 1].min() >= c - eps and local[:, 1].max() <= dd + eps)
                coverage |= bool(inside and support_rays(sim, body, region, points))
        valid = bool(load and coverage and height_error <= config.support_contact_tolerance_m and tilt <= config.max_tilt_rad)
        rows.append({'instance': name, 'valid': valid, 'load_bearing_contact': load,
                     'height_error_m': height_error, 'tilt_rad': tilt,
                     'coverage': coverage, 'contacts': contacts})
    return {'valid': all(r['valid'] for r in rows), 'objects': rows}


def contact_minima(sim):
    result = {}
    for c in sim.data.contact:
        # Names of geoms can be empty; stable body name plus within-body index avoids conflation.
        def identity(g):
            b = int(sim.model.geom_bodyid[g])
            return (sim.model.body(b).name, int(g) - int(sim.model.body_geomadr[b]))
        key = tuple(sorted((identity(c.geom1), identity(c.geom2))))
        result[key] = min(result.get(key, 0.), float(c.dist))
    return result


def matched_source_effects(control, candidate, config):
    if not np.isclose(control.data.time, candidate.data.time, atol=1e-8, rtol=0):
        raise ValueError('matched control must have the same simulation time')
    rows = []
    for entry in control.catalog:
        name = entry['mjcf_body']
        if name.startswith(control.robot.config.namespace):
            continue
        b0, b1 = control.model.body(name).id, candidate.model.body(name).id
        if not control.model.body_dofnum[b0]:
            continue
        delta = float(np.linalg.norm(candidate.data.xpos[b1] - control.data.xpos[b0]))
        rotation = quat_angle(candidate.data.xquat[b1], control.data.xquat[b0])
        rows.append({'instance': name, 'delta_vs_control_m': delta, 'rotation_vs_control_rad': rotation,
                     'valid': delta <= config.source_effect_tolerance_m and rotation <= config.source_effect_rotation_rad})
    c0, c1 = contact_minima(control), contact_minima(candidate)
    added_contacts = []
    for key, value in c1.items():
        if any(name.startswith('procedural_obstacle_') for name, _ in key):
            continue
        if value < -config.collision_tolerance_m and value < c0.get(key, 0.) - config.collision_tolerance_m:
            added_contacts.append({'pair': key, 'candidate_distance_m': value, 'control_distance_m': c0.get(key)})
    return {'valid': all(r['valid'] for r in rows) and not added_contacts,
            'same_time_s': float(control.data.time), 'source_objects': rows,
            'new_or_worse_source_penetrations': added_contacts,
            'scope': 'unedited_dynamic_source_bodies_and_source_contact_pairs_vs_equal_time_unedited_control'}


def validate_augmented_scene(sim, placements, settling, control, config):
    finite = bool(np.isfinite(sim.state_vector()).all())
    collision = collision_pairs(sim, placements, config, settled=True)
    support = load_support(sim, placements, config)
    effects = matched_source_effects(control, sim, config)
    stable = bool(settling['stable'])
    healthy = finite and not settling['new_warnings']
    case = case_construction_check(sim, placements, config)
    return {'valid': bool(stable and healthy and collision['valid'] and support['valid'] and effects['valid'] and case['valid']),
            'stability': stable, 'solver_health': healthy, 'settling': settling,
            'collision': collision, 'support': support, 'source_effects': effects,
            'source_absolute_penetrations_recorded_not_claimed_absent': settling['severe_penetration'],
            'case_construction': case, 'case_condition': 'construction_satisfied' if case['valid'] and config.case_policy else 'unknown',
            'task_completion': 'unknown'}
