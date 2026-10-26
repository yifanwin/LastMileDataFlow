"""Preserve same-XY explicit yaw contrasts within the shared pose budget."""
import numpy as np

from .base import ModeBuilder
from .geometry import lattice_points, nominal
from ..build.proposal import BasePoseEdit, BuildProposal, ProbeResult
from ..stations.sampling import yaw_variants


class OrientationBuilder(ModeBuilder):
    def candidates(self, ctx, rng):
        rows = lattice_points(ctx.geometry)
        for index in rng.permutation(len(rows)):
            base = nominal(ctx, rows[int(index)])
            if base['geometry'] != 'valid':
                continue
            group = yaw_variants(base, (-np.deg2rad(ctx.settings.orientation_delta_deg), np.deg2rad(ctx.settings.orientation_delta_deg)))
            valid = []
            for candidate in group:
                checked = nominal(ctx, candidate)
                visibility = checked.get('visibility_proxy', {})
                if checked['geometry'] == 'valid' and (visibility.get('known_max') or 0.) > ctx.settings.visibility_low:
                    valid.append({key: checked[key] for key in ('station_id', 'xy_id', 'xy', 'grid_index', 'initial', 'yaw', 'yaw_source') if key in checked})
            if len(valid) >= 2:
                yield BuildProposal('yaw-' + base['xy_id'], 'orientation', [r['station_id'] for r in valid],
                                    pose_edit=BasePoseEdit(valid), metadata={'xy_id': base['xy_id'], 'yaw_group_ids': [r['station_id'] for r in valid]})

    def cheap_validate(self, ctx, proposal, *, sim=None, geometry=None, stations=None):
        rows = proposal.pose_edit.stations if stations is None else [r for r in stations if r['station_id'] in proposal.metadata['yaw_group_ids']]
        if stations is not None:
            rows = [r for r in rows if (r['visibility'].get('known_max') or 0.) > ctx.settings.visibility_low]
        yaws = [r.get('yaw', r['initial'][ctx.baseline.robot.initial_base_field][2]) for r in rows]
        valid = len(rows) >= 2 and len({round(yaw, 6) for yaw in yaws}) >= 2 and len({r['xy_id'] for r in rows}) == 1
        return ProbeResult('pass' if valid else 'fail', 'same_xy_visible_yaw_contrast' if valid else 'no_visible_yaw_contrast',
                           {'pose_count': len(rows), 'scope': 'initial_pose_contrast_no_success_rate_claim'})
