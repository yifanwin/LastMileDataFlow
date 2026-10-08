"""Strict case factory settings; paths resolve against the settings file."""
from pathlib import Path
import math
from ..io import read_json


def load_factory_config(path):
    path=Path(path).resolve(); value=read_json(path)
    required={'schema_version','capability_path','grasp_root','robot_planner_dir','seed','max_grasps','max_stations',
              'spacing_m','edge_gap_m','torso_heights','num_ik_seeds','max_attempts','station_timeout_s','timeout_s',
              'min_target_pixels','yaw_offsets_rad','render_video'}
    if not isinstance(value,dict) or set(value)!=required or value['schema_version']!='case-factory-config-v1':
        raise ValueError('unknown/missing factory settings')
    for key in ('capability_path','grasp_root','robot_planner_dir'):
        if not isinstance(value[key],str) or not value[key]: raise ValueError('explicit '+key+' required')
        value[key]=str((path.parent/value[key]).resolve())
    for key in ('max_grasps','max_stations','num_ik_seeds','max_attempts','min_target_pixels','seed'):
        if type(value[key]) is not int or value[key] < (0 if key=='seed' else 1): raise ValueError('invalid '+key)
    for key in ('spacing_m','edge_gap_m','station_timeout_s','timeout_s'):
        if type(value[key]) not in (float,int) or not math.isfinite(value[key]) or value[key] <= 0: raise ValueError('invalid '+key)
    h=value['torso_heights']; y=value['yaw_offsets_rad']
    if not isinstance(h,list) or len(set(h))<2 or any(type(x) not in (float,int) or not math.isfinite(x) or not 0 <= x <= .738 for x in h):
        raise ValueError('torso must be enabled')
    if not isinstance(y,list) or not y or any(type(x) not in (float,int) or not math.isfinite(x) or abs(x)>math.pi for x in y):
        raise ValueError('finite yaw coverage required')
    if type(value['render_video']) is not bool: raise ValueError('invalid render flag')
    return value
