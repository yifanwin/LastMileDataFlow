"""Workspace-edge intent with a same-support target relocation fallback."""
import numpy as np

from .base import ModeBuilder
from .geometry import lattice_points
from ..build.proposal import BuildProposal, MoveTargetOnSupport, ProbeResult
from ..geometry import support_rays
from ..stations.sampling import disk_samples


class ReachabilityBuilder(ModeBuilder):
    def candidates(self, ctx, rng):
        rows = lattice_points(ctx.geometry)
        anchor = np.asarray(ctx.task['anchor_world'])
        ranked = sorted(rows, key=lambda r: np.linalg.norm(np.asarray(r['xy']) - anchor[:2]), reverse=True)
        if len(ranked) < 2:
            return
        edge = ctx.station_config.radius_m - ctx.station_config.spacing_m
        for row in ranked[:8]:
            if np.linalg.norm(np.asarray(row['xy']) - anchor[:2]) < edge:
                break
            yield BuildProposal('natural-reach-' + row['station_id'], 'reachability', [row['station_id'], ranked[-1]['station_id']],
                                metadata={'proxy': 'relative_workspace_edge', 'local_base_radius_m': ctx.execution.local_base_radius_m})
        if ctx.task['operation'] != 'pick':
            return
        body = ctx.baseline.target_id
        points = ctx.geometry.target_points
        for support in ctx.geometry.supports:
            for direction in rng.permutation(np.array([[1., 0.], [-1., 0.], [0., 1.], [0., -1.]])):
                delta = np.r_[direction * ctx.station_config.spacing_m, 0.]
                shifted = points + delta
                local = support.local(shifted)
                a, b, c, d = support.bounds
                if not (local[:, 0].min() >= a + .01 and local[:, 0].max() <= b - .01
                        and local[:, 1].min() >= c + .01 and local[:, 1].max() <= d - .01
                        and support_rays(ctx.baseline, body, support, shifted)):
                    continue
                pose = {'position': (ctx.baseline.data.xpos[body] + delta).tolist(),
                        'quaternion_wxyz': ctx.baseline.data.xquat[body].tolist()}
                yield BuildProposal('support-reach', 'reachability', edits=[MoveTargetOnSupport(ctx.task['target_body'], pose, support)],
                                    metadata={'target_translation_m': delta.tolist(), 'proxy': 'support_relocation_and_workspace_edge'})

    def cheap_validate(self, ctx, proposal, *, sim=None, geometry=None, stations=None):
        geometry = ctx.geometry if geometry is None else geometry
        rows = lattice_points(geometry) if stations is None else stations
        anchor = np.asarray(geometry.task['anchor_world'][:2])
        moves = proposal.runtime_changes()['target_moves']
        if sim is None and moves:
            anchor = anchor + np.asarray(moves[0]['pose']['position'])[:2] - ctx.baseline.data.xpos[ctx.baseline.target_id, :2]
            rows = disk_samples(anchor, ctx.station_config.radius_m, ctx.station_config.spacing_m)
            grid = geometry.navigation
            rows = [r for r in rows if grid.cell(r['xy']) is not None and not geometry.occupancy.occupied[grid.cell(r['xy'])]]
        if len(rows) < 2:
            return ProbeResult('fail', 'no_workspace_position_contrast')
        distances = [float(np.linalg.norm(np.asarray(r['xy']) - anchor)) for r in rows]
        edge = max(distances) >= ctx.station_config.radius_m - ctx.station_config.spacing_m
        reach = ctx.baseline.robot.config.arm_working_reach_m
        return ProbeResult('pass' if edge else 'fail', 'workspace_edge' if edge else 'workspace_interior_only',
            {'initial_range_m': [min(distances), max(distances)], 'arm_reach_m': reach,
             'local_base_radius_m': ctx.execution.local_base_radius_m,
             'scope': 'coarse_edge_proxy_with_mobile_base_not_kinematic_unreachability'})
