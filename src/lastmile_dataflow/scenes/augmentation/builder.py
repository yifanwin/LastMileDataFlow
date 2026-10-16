"""Bounded obstacle placement with actual settling and case checks."""
import time

import numpy as np
from ...utils.logging_config import trial_progress

from .sampling import sample_placement, case_assets, surface_occupied_bounds, sample_surface_point
from ...data.rendering.edit_views import ViewConfig
from ...data.rendering.obstacle_views import render_obstacle_pair
from ...runtime.construction.session import matched_control
from ..graph import build_scene_graph
from ..stations.sampling import sample_stations
from ..dataset import select_stations
from .checks import collision_pairs, validate_augmented_scene


def build_variant(session, baseline, task, config, geometry_config, pool, geometry, regions,
             surfaces, baseline_stations, path, variant_id, seed):
    """有界放置、真实窄相/静置检查；放不下就保留负结果。"""
    path.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(seed)
    deadline = time.monotonic() + config.variant_timeout_s
    policy = config.case_policy
    count = int(rng.integers(*[config.obstacle_count[0], config.obstacle_count[1] + 1]))
    result = {'scene_id': variant_id, 'seed': seed, 'status': 'rejected', 'requested_count': count}
    occupied = surface_occupied_bounds(baseline, task)
    try:
        area = sum((r.bounds[1] - r.bounds[0]) * (r.bounds[3] - r.bounds[2])
                   for r in {p['region'].region_id: p['region'] for p in surfaces}.values())
        assets = case_assets(rng, count, pool, geometry, policy, config.mandatory_categories, surface_area_m2=area)
    except ValueError as exc:
        return {**result, 'reason': str(exc)}
    placements = []
    physical_trials = 0
    for candidate_index in range(config.max_candidates):
        trial_progress('放置进度 当前提案物体=%s/%s 当前候选尝试=%s/%s 已做物理试验=%s/%s',
                       len(placements), count, candidate_index + 1, config.max_candidates,
                       physical_trials, config.max_physical_trials)
        if time.monotonic() > deadline:
            return {**result, 'reason': 'variant_timeout'}
        asset = assets[len(placements)]
        ground = asset['category'] in pool['ground']
        points = regions['points'] if ground else surfaces
        if not points:
            return {**result, 'reason': 'no_placement_points'}
        if ground:
            point = points[int(rng.integers(len(points)))]
            yaw = float(rng.uniform(-np.pi, np.pi))
        else:
            point, yaw = sample_surface_point(rng, asset, surfaces, placements, occupied, policy, config.placement_gap_m)
            if point is None:
                continue
        proposal, reason = sample_placement(baseline, asset, point, yaw, config,
                                           region=None if ground else point['region'])
        if proposal is None:
            continue
        proposed = placements + [proposal]
        sim = session.candidate(proposed, deadline=deadline)
        if not collision_pairs(sim, proposed, config)['valid']:
            session.reject()
            continue
        placements = proposed
        if len(placements) < count:
            continue
        physical_trials += 1
        settling = session.settle(deadline=deadline)
        control, _ = matched_control(baseline, settling['simulated_s'], session.config, deadline)
        try:
            checks = validate_augmented_scene(sim, placements, settling, control, config)
            if not checks['valid']:
                placements = []
                session.reject()
                if physical_trials >= config.max_physical_trials:
                    return {**result, 'reason': 'physical_trial_budget_exhausted'}
                continue
            stations = sample_stations(sim, task, geometry_config, frozen_stations=baseline_stations)
            method = policy.station_selection if policy else 'coverage'
            limit = policy.max_final_stations if policy else 20
            selected, _ = select_stations(stations, task['anchor_world'], method, limit)
            if len(selected) < (policy.min_final_points if policy else 1):
                return {**result, 'reason': 'too_few_stations'}
            for i, proposal in enumerate(placements):
                body = sim.model.body(f'procedural_obstacle_{i}').id
                proposal['settled_pose'] = {'position': sim.data.xpos[body].tolist(), 'quaternion_wxyz': sim.data.xquat[body].tolist()}
            sim.freeze(path / 'scene')
            # Save the actual no-addition control before its lifetime ends. Sampling
            # uses the same lattice and final selection policy as the edited scene.
            before_stations, _ = select_stations(
                sample_stations(control, task, geometry_config, frozen_stations=baseline_stations),
                task['anchor_world'], method, limit,
            )
            control.freeze(path / 'before-scene')
            if config.record_views:
                before_graph = build_scene_graph(control, config=session.config.graph_config())
                after_graph = build_scene_graph(sim, stage='settled', config=session.config.graph_config())
                render_obstacle_pair(control, sim, before_graph, after_graph,
                    {f'procedural_obstacle_{i}' for i in range(count)}, task['target_body'],
                    {task['target_body']}, path / 'images', sample_id=variant_id,
                    config=ViewConfig(**config.views), min_pixels=geometry_config.min_target_pixels)
            return {**result, 'status': 'accepted', 'task': task, 'stations': selected,
                    'placements': placements, 'before_stations': before_stations, 'case': policy.case if policy else 'procedural',
                    'actual_count': len(placements)}
        finally:
            control.close()
    return {**result, 'reason': 'candidate_budget_exhausted'}
