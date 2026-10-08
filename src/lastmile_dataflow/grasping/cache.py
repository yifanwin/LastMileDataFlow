"""Immutable grasp cache identity and verification boundary."""
from pathlib import Path
import numpy as np
from ..io import read_json, write_json, digest, file_digest


def _validate(candidates):
    if not isinstance(candidates,list) or not candidates: raise ValueError('nonempty candidates required')
    for row in candidates:
        pose=np.asarray(row['transform'],float)
        if pose.shape!=(4,4) or not np.isfinite(pose).all() or not np.allclose(pose[3],[0,0,0,1]) or not np.allclose(pose[:3,:3].T@pose[:3,:3],np.eye(3),atol=1e-5) or not np.isclose(np.linalg.det(pose[:3,:3]),1,atol=1e-5):
            raise ValueError('invalid object-local transform')
        if type(row.get('simulation_pass')) is not bool or row.get('region') not in ('body','handle'):
            raise ValueError('explicit simulation/region status required')
        if type(row.get('width_m')) not in (float,int) or not np.isfinite(row['width_m']) or row['width_m'] <= 0 or type(row.get('score')) not in (float,int) or not np.isfinite(row['score']):
            raise ValueError('invalid width/score')
        if row['simulation_pass'] and not row.get('simulation_evidence'):
            raise ValueError('verified candidate needs lift/shake simulation evidence')


def save_grasp_cache(path, *, asset_id, asset_digest, gripper_digest, candidates):
    _validate(candidates)
    path=Path(path); path.mkdir(parents=True,exist_ok=False)
    with (path/'grasps.npz').open('xb') as f:
        np.savez_compressed(f,transforms=np.array([r['transform'] for r in candidates]))
    packet={'schema_version':'rby1-grasps-v1','robot':'rby1m','frame':'object','asset_id':asset_id,
            'asset_digest':asset_digest,'gripper_digest':gripper_digest,'candidates':candidates,
            'npz_sha256':file_digest(path/'grasps.npz')}
    packet['cache_digest']=digest(packet); write_json(path/'manifest.json',packet)
    return path


def load_grasp_cache(path, *, gripper_digest, asset_id=None, asset_digest=None, region=None, require_verified=True):
    path=Path(path); packet=read_json(path/'manifest.json'); raw=dict(packet); identity=raw.pop('cache_digest')
    if digest(raw)!=identity or packet['schema_version']!='rby1-grasps-v1' or packet['robot']!='rby1m' or packet['frame']!='object':
        raise ValueError('invalid RBY-1 cache identity')
    if packet['gripper_digest']!=gripper_digest or asset_id is not None and packet['asset_id']!=asset_id or asset_digest is not None and packet['asset_digest']!=asset_digest:
        raise ValueError('stale or incompatible grasp cache')
    if file_digest(path/'grasps.npz')!=packet['npz_sha256']: raise ValueError('modified grasp transforms')
    _validate(packet['candidates'])
    with np.load(path/'grasps.npz',allow_pickle=False) as data:
        if not np.array_equal(data['transforms'],np.array([r['transform'] for r in packet['candidates']])):
            raise ValueError('manifest/transform mismatch')
    rows=[dict(r,grasp_id=i) for i,r in enumerate(packet['candidates']) if (region is None or r['region']==region) and (not require_verified or r['simulation_pass'])]
    if not rows: raise ValueError('no verified grasps in requested region' if require_verified else 'empty grasp subset')
    return packet,rows
