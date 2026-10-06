"""Endpoint-only rule checks for general construction; no neighbor protection ACL."""
import numpy as np
import mujoco
from ..construction.case_schema import CheckResult, required_checks_pass
from .predicates import evaluate_conditions


def endpoint_checks(template, proposal, graph, *, endpoint, bindings=None):
    bindings = bindings or proposal.bindings
    checks = []
    added_roles = {o['bind_as'][1:] for o in proposal.operations if o['op'] == 'add'}
    for role, spec in template.roles.items():
        if spec.required and not (endpoint == 'before' and role in added_roles):
            node = graph.nodes.get(bindings.get(role))
            status = 'pass' if node is not None else 'fail'
            reason = 'required_role_present' if node is not None else 'required_role_missing'
            if node is not None and spec.type in ('robot_station', 'station') and node.get('kind') != 'station':
                status, reason = 'fail', 'station_node_required'
            if node is not None and spec.type in ('support_surface', 'surface') and node.get('support_capability') not in ('planar', 'horizontal_plane'):
                status, reason = 'unknown', 'support_capability_unknown'
            if node is not None and spec.type in ('object', 'manipulable_object') and node.get('kind') != 'object':
                status, reason = 'fail', 'object_node_required'
            checks.append(CheckResult('role:' + role, status, reason=reason))
    conditions = proposal.effective_invariants(template)
    if endpoint == 'after':
        conditions += proposal.effective_requirements(template)
    elif endpoint != 'before':
        raise ValueError('unknown endpoint')
    checks += evaluate_conditions(conditions, graph, bindings=bindings, parameters=template.parameters)
    return checks


def physical_checks(sim, graph, settling, *, affected, config, before_graph=None):
    checks = [CheckResult('stability', 'pass' if settling['stable'] else 'fail', evidence=settling),
              CheckResult('solver_health', 'fail' if settling['new_warnings'] else 'pass', evidence={'warnings': settling['new_warnings']}),
              CheckResult('severe_contact_penetration', 'fail' if settling['severe_penetration'] else 'pass',
                          evidence={'contacts': settling['severe_penetration']})]
    # Only affected/changed objects and their neighborhood need new support proof.
    # Unsupported unrelated geometry does not become a new permission restriction.
    affected_bounds = [graph.nodes[n].get('collision_bounds_world') for n in affected if n in graph.nodes]
    affected_bounds = [np.asarray(b) for b in affected_bounds if b is not None]
    for name, node in graph.nodes.items():
        if node.get('kind') == 'object' and node.get('root_motion') == 'free':
            nearby = name in affected
            bounds = node.get('collision_bounds_world')
            if bounds is not None:
                lo, hi = np.asarray(bounds)
                nearby |= any(np.all(hi >= b[0]-.1) and np.all(lo <= b[1]+.1) for b in affected_bounds)
            previous = before_graph.nodes.get(name) if before_graph is not None else None
            if previous and previous.get('pose'):
                nearby |= np.linalg.norm(np.asarray(node['pose']['position'])-previous['pose']['position']) > config.drift_m
            if not nearby:
                continue
            status = node.get('support_status', 'unknown')
            checks.append(CheckResult('physical_support:' + name, status,
                          reason=node.get('support_reason', 'support_unknown')))
    # MuJoCo does not generate contacts for static-static pairs. Check moved
    # furniture against nearby unrelated collision geoms using narrow-phase distance.
    m, d = sim.model, sim.data
    affected_bodies = {m.body(name).id for name in affected if name in graph.nodes and graph.nodes[name].get('mjcf_body')}
    top_roots = []
    for b in range(m.nbody):
        root = b
        while int(m.body_parentid[root]) != 0:
            root = int(m.body_parentid[root])
        top_roots.append(root)
    severe = []
    for g in range(m.ngeom):
        if top_roots[int(m.geom_bodyid[g])] not in affected_bodies or not (m.geom_contype[g] or m.geom_conaffinity[g]):
            continue
        distances = np.linalg.norm(d.geom_xpos-d.geom_xpos[g], axis=1)
        neighbors = np.flatnonzero(distances <= m.geom_rbound + m.geom_rbound[g] + config.penetration_m)
        for h in neighbors:
            if h <= g and int(m.geom_bodyid[h]) in affected_bodies or h == g:
                continue
            if int(m.geom_bodyid[h]) == int(m.geom_bodyid[g]) or not (m.geom_contype[h] or m.geom_conaffinity[h]):
                continue
            if top_roots[int(m.geom_bodyid[h])] == top_roots[int(m.geom_bodyid[g])]:
                continue
            if not (m.geom_contype[g] & m.geom_conaffinity[h] or m.geom_contype[h] & m.geom_conaffinity[g]):
                continue
            if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE or m.geom_type[h] == mujoco.mjtGeom.mjGEOM_PLANE:
                continue  # dynamic-plane contacts were checked above
            # Same rigid welded assembly is not a scene collision.
            if m.body_weldid[m.geom_bodyid[g]] == m.body_weldid[m.geom_bodyid[h]] and m.body_weldid[m.geom_bodyid[g]] != 0:
                continue
            distance = mujoco.mj_geomDistance(m, d, g, int(h), config.penetration_m, None)
            if distance < -config.penetration_m:
                severe.append({'geoms': [m.geom(g).name, m.geom(int(h)).name], 'distance_m': float(distance)})
    checks.append(CheckResult('nearby_geometry_penetration', 'fail' if severe else 'pass', evidence={'pairs': severe}))
    return checks


def rules_pass(checks):
    return required_checks_pass(checks)
