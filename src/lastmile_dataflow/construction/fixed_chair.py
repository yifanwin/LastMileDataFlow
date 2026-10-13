"""Fixed world root XY, random yaw; ordinary collision and settling still apply."""
import math
import numpy as np


def validate_fixed_chair(value):
    if not isinstance(value, dict):
        raise ValueError('fixed Chair must be a dictionary')
    if not value:
        return
    if (not isinstance(value, dict) or set(value) != {'asset_id','root_xy','position_tolerance_m','source_model_sha256'}
            or not isinstance(value['asset_id'], str) or not value['asset_id']
            or len(value['root_xy']) != 2 or not np.isfinite(value['root_xy']).all()
            or not math.isfinite(value['position_tolerance_m']) or not 0 < value['position_tolerance_m'] <= .006
            or len(value['source_model_sha256']) != 64):
        raise ValueError('invalid fixed Chair anchor')


def fixed_chair_point(local_points, root_xy, yaw):
    # Existing placement aligns the footprint center, not the asset root.
    c, s = np.cos(yaw), np.sin(yaw)
    rotation = np.array([[c,-s,0.],[s,c,0.],[0.,0.,1.]])
    rotated = np.asarray(local_points) @ rotation.T
    center = (rotated[:,:2].min(axis=0)+rotated[:,:2].max(axis=0))/2
    return {'point_id':'fixed-chair-root-xy', 'xy':(np.asarray(root_xy)+center).tolist(),
            'sources':[{'kind':'fixed_chair_root_xy','root_xy':list(root_xy)}]}


def check_fixed_chair(sim, placements, value):
    if not value:
        return {'valid':True, 'applied':False}
    chairs = [(i,p) for i,p in enumerate(placements) if p['kind']=='ground' and p['category']=='Chair']
    if len(chairs) != 1 or chairs[0][1]['asset_id'] != value['asset_id']:
        return {'valid':False, 'reason':'fixed_chair_asset_or_count_mismatch'}
    index, placement = chairs[0]
    actual = sim.data.xpos[sim.model.body(f'procedural_obstacle_{index}').id][:2]
    drift = float(np.linalg.norm(actual-np.asarray(value['root_xy'])))
    proposal_error = float(np.linalg.norm(np.asarray(placement['pose']['position'][:2])-value['root_xy']))
    return {'valid':proposal_error <= 1e-12 and drift <= value['position_tolerance_m'],
            'applied':True, 'asset_id':value['asset_id'], 'root_xy':value['root_xy'],
            'actual_root_xy':actual.tolist(), 'proposal_error_m':proposal_error,
            'settled_drift_m':drift, 'tolerance_m':value['position_tolerance_m']}
