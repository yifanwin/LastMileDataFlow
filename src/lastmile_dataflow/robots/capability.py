"""Measured RBY-1 geometry and conservative reach-table lookup.

Reach tables only prefilter. They cannot replace per-station IK or prove failure.
"""
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import mujoco
from ..io import digest, file_digest, read_json, write_json
from ..scenes.mjcf import load_spec
from ..scenes.geometry import geom_points
from .rby1 import RBY1Adapter, prepare_robot_spec


def measure_capability(robot_config):
    spec=load_spec(robot_config.model_path); prepare_robot_spec(spec,robot_config)
    model=spec.compile(); data=mujoco.MjData(model); robot=RBY1Adapter(model,data,robot_config)
    robot.initialize(); sim=SimpleNamespace(model=model,data=data)
    base=model.body('robot_0/base').id; base_rotation=data.xmat[base].reshape(3,3)
    chassis=[]
    for g in range(model.ngeom):
        if not (model.geom_contype[g] or model.geom_conaffinity[g]): continue
        points=(geom_points(sim,g)-data.xpos[base])@base_rotation
        # Bottom chassis envelope; excludes raised articulated manipulation links.
        if points[:,2].min() < .25: chassis.extend(points)
    if not chassis: raise ValueError('base collision footprint missing')
    chassis=np.asarray(chassis); lo,hi=chassis[:,:2].min(axis=0),chassis[:,:2].max(axis=0)
    initial={k:list(v) for k,v in robot_config.initial.items()}; initial['left_gripper']=[robot_config.gripper_limits[0]]
    robot.initialize(initial); site=model.site('robot_0/ee_site_l').id
    rotation=data.site_xmat[site].reshape(3,3); origin=data.site_xpos[site]
    fingers=[]
    for i in (1,2):
        b=model.body(f'robot_0/ee_finger_l{i}').id
        gs=[g for g in range(model.ngeom) if model.geom_bodyid[g]==b and (model.geom_contype[g] or model.geom_conaffinity[g])]
        if not gs: raise ValueError('finger collision geometry missing')
        fingers.append((np.concatenate([geom_points(sim,g) for g in gs])-origin)@rotation)
    fingers.sort(key=lambda p:float(p[:,1].mean()))
    opening=float(fingers[1][:,1].min()-fingers[0][:,1].max())
    if opening<=0: raise ValueError('RBY TCP y closing-axis convention mismatch')
    buffer=np.empty(mujoco.mj_sizeModel(model),dtype=np.uint8); mujoco.mj_saveModel(model,None,buffer)
    import hashlib
    gripper={'closing_axis_tcp':'y','approach_axis_tcp':'z','max_opening_m':opening,
             'finger_width_m':float(max(np.ptp(p[:,0]) for p in fingers)),
             'finger_length_m':float(max(np.ptp(p[:,2]) for p in fingers)),
             'finger_open_positions_m':list(robot_config.gripper_limits),
             'collision_geometry':'native RBY-1 finger and palm meshes, not DROID'}
    gripper['digest']=digest({'gripper':gripper,'compiled_robot':hashlib.sha256(buffer.tobytes()).hexdigest()})
    return {'schema_version':'robot-capability-v1','robot':'rby1m','measurement_status':'measured',
            'model_sha256':file_digest(robot_config.model_path),'compiled_model_sha256':hashlib.sha256(buffer.tobytes()).hexdigest(),
            'base_footprint_m':{'bounds_xy':[lo.tolist(),hi.tolist()], 'width_m':float(hi[0]-lo[0]),
                                'depth_m':float(hi[1]-lo[1]),'rotation_radius_m':float(np.linalg.norm(chassis[:,:2],axis=1).max()),
                                'method':'conservative_low_collision_envelope_z_below_0.25m_in_base_frame'},
            'gripper':gripper,'reach_table':None}


def save_capability(path, robot_config):
    path=Path(path)
    if path.exists(): raise FileExistsError(path)
    packet=measure_capability(robot_config); write_json(path,packet); return packet


class ReachTable:
    def __init__(self, path, *, robot_digest):
        self.path=Path(path); self.packet=read_json(path)
        if self.packet.get('schema_version')!='reach-table-v1' or self.packet.get('robot_digest')!=robot_digest:
            raise ValueError('stale reach calibration')
        self.rows=self.packet['samples']
        if not self.rows: raise ValueError('empty reach table')
        self.xyz=np.array([r['xyz_base'] for r in self.rows],float)
        if self.xyz.shape!=(len(self.rows),3) or not np.isfinite(self.xyz).all(): raise ValueError('invalid reach samples')

    def query(self, base, xyz_world):
        base=np.asarray(base,float); point=np.asarray(xyz_world,float).copy()
        point[:2]-=base[:2]; c,s=np.cos(base[2]),np.sin(base[2]); point[:2]=np.array([[c,s],[-s,c]])@point[:2]
        distances=np.linalg.norm(self.xyz-point,axis=1); i=int(np.argmin(distances)); distance=float(distances[i])
        if distance>self.packet['resolution_m']/2:
            return {'status':'boundary','margin_m':-distance,'use':'prefilter_only'}
        row=self.rows[i]
        return {'status':'reachable' if row['reach']=='success' else 'boundary' if row['reach']!='no_solution' else 'unreachable',
                'margin_m':self.packet['resolution_m']/2-distance,'use':'prefilter_only','sample':i}
