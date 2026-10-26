"""One local repair may adjust poses or substitute a similarly sized role asset."""
import mujoco
import numpy as np

from ..geometry import quat_angle


def repair_allowed(ctx, original, repaired):
    if (repaired.proposal_intent != original.proposal_intent
            or len(repaired.edits) != len(original.edits) or repaired.pose_edit != original.pose_edit):
        return False
    before, after = original.runtime_changes(), repaired.runtime_changes()
    profiles = {p.asset_id: p for p in ctx.selector.profiles}
    for key in ('placements', 'target_moves'):
        if len(before[key]) != len(after[key]):
            return False
        for a, b in zip(before[key], after[key]):
            if any(a.get(field) != b.get(field) for field in ('body', 'kind', 'support_instance', 'support_geom')):
                return False
            if np.linalg.norm(np.asarray(a['pose']['position']) - b['pose']['position']) > ctx.settings.repair_translation_m + 1e-8:
                return False
            qa, qb = np.asarray(a['pose']['quaternion_wxyz']), np.asarray(b['pose']['quaternion_wxyz'])
            if quat_angle(qa, qb) > np.deg2rad(ctx.settings.repair_yaw_deg) + 1e-8:
                return False
            ra, rb = np.zeros(9), np.zeros(9)
            mujoco.mju_quat2Mat(ra, qa)
            mujoco.mju_quat2Mat(rb, qb)
            relative = rb.reshape(3, 3) @ ra.reshape(3, 3).T
            if not np.allclose(relative[:, 2], [0, 0, 1], atol=1e-6):
                return False
            if key == 'placements' and a['asset_id'] != b['asset_id']:
                pa, pb = profiles[a['asset_id']], profiles[b['asset_id']]
                if not set(pa.roles) & set(pb.roles):
                    return False
                sa = np.array([pa.width_m, pa.depth_m, pa.height_m])
                sb = np.array([pb.width_m, pb.depth_m, pb.height_m])
                if not np.all(np.abs(sb / sa - 1) <= .2):
                    return False
    return True
