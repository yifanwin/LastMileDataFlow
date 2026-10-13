"""Deterministic capacity evidence over the declared discrete placement domain.

Exhausting a random proposal budget is NEVER a capacity certificate. This module
checks every allowed asset/point/yaw, with real narrowphase supplied by the caller.
The evidence is not a mathematical claim about all continuous poses in a room.
"""
from collections import Counter
import time
import numpy as np
from ..io import digest
from .obstacle_sampling import sample_placement


def state_binding(sim, placements):
    return digest({'qpos': sim.data.qpos, 'qvel': sim.data.qvel,
                   'placement_count': len(placements)})


def surface_yaws(region, policy):
    axes = np.asarray(region.axes)
    alignment = float(np.arctan2(axes[1, 0], axes[0, 0]))
    return [float(alignment + k*np.pi/2 + jitter) for k in range(4)
            for jitter in (-policy.surface_alignment_jitter_rad, 0., policy.surface_alignment_jitter_rad)]


def probe_capacity(sim, placements, assets, points, config, collision_test, *, deadline=None):
    """Return (evidence, legal_asset, legal_proposal); unknown never means full."""
    policy = config.case_policy
    ordered = sorted(assets, key=lambda g: (g['size']['width']*g['size']['depth'], g['size']['height'], g['asset_id']))
    domain = {'asset_ids': [g['asset_id'] for g in ordered],
              'assets': [{'asset_id': g['asset_id'], 'size': g['size'], 'local_points_sha256': digest(g['local_points'])} for g in ordered],
              'points': [{'point_id': p['point_id'], 'xy': p['xy'], 'region': p['region'].to_dict(),
                          'yaws': surface_yaws(p['region'], policy)} for p in points]}
    total = len(ordered)*len(points)*12
    evidence = {'schema_version': 'surface-capacity-v1', 'status': 'inconclusive', 'exhaustive': False,
                'scope': 'configured_assets_and_discrete_support_point_yaw_domain_not_all_continuous_poses',
                'state_sha256': state_binding(sim, placements), 'placement_count': len(placements),
                'domain': domain, 'domain_sha256': digest(domain), 'total_poses': total, 'tested_poses': 0,
                'minimum_asset_id': ordered[0]['asset_id'] if ordered else None}
    reasons = Counter(); start = time.monotonic()
    try:
        if not ordered:
            evidence['reason'] = 'no_qualified_surface_assets'
            return evidence, None, None
        for asset in ordered:
            for point in points:
                for yaw in surface_yaws(point['region'], policy):
                    if evidence['tested_poses'] >= policy.capacity_probe_budget or deadline is not None and time.monotonic() >= deadline:
                        evidence['reason'] = 'capacity_probe_budget_or_deadline_exhausted'
                        return evidence, None, None
                    evidence['tested_poses'] += 1
                    proposal, reason = sample_placement(sim, asset, point, yaw, config, region=point['region'])
                    if proposal is None:
                        reasons[reason] += 1
                        continue
                    if collision_test(proposal):
                        evidence.update(status='legal_placement_found', reason='surface_not_exhausted', legal_asset_id=asset['asset_id'])
                        return evidence, asset, proposal
                    reasons['full_height_collision'] += 1
        evidence.update(status='domain_exhausted', exhaustive=True, reason='no_legal_surface_placement_in_declared_domain')
        return evidence, None, None
    finally:
        evidence.update(rejection_counts=dict(reasons), elapsed_s=time.monotonic()-start)


def valid_certificate(sim, placements, evidence):
    if not isinstance(evidence, dict):
        return False
    domain = evidence.get('domain', {})
    return (evidence.get('schema_version') == 'surface-capacity-v1'
            and evidence.get('status') == 'domain_exhausted' and evidence.get('exhaustive') is True
            and bool(domain.get('asset_ids')) and evidence.get('minimum_asset_id') in domain['asset_ids']
            and evidence.get('total_poses') == len(domain.get('asset_ids', []))*len(domain.get('points', []))*12
            and evidence.get('tested_poses') == evidence.get('total_poses')
            and evidence.get('domain_sha256') == digest(domain)
            and evidence.get('state_sha256') == state_binding(sim, placements)
            and evidence.get('placement_count') == len(placements))
