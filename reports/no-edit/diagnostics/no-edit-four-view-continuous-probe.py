"""Real continuous smoke only; does NOT publish a partial station map as a dataset task."""
import os,json,uuid
from pathlib import Path
from lastmile_dataflow.workflows.no_edit import available_gpus
available=available_gpus(list(range(8)))
if not available: raise RuntimeError('No idle permitted GPU')
gpu=available[0]['index']; os.environ['CUDA_VISIBLE_DEVICES']=str(gpu);os.environ['MUJOCO_GL']='egl';os.environ['MUJOCO_EGL_DEVICE_ID']=str(gpu)
print('GPU',gpu,flush=True)
import numpy as np
from lastmile_dataflow.config import RobotConfig,CollectionConfig
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.no_edit_execution import run_raw_attempt
from lastmile_dataflow.tasks.raw_scene import grasp_candidates
from lastmile_dataflow.navigation.astar import Grid,search
from lastmile_dataflow.io import read_json,write_json
root=Path('outputs/no_edit/no-edit-val-four-view-probe-20261010').resolve();scene=root/'scenes/val_103'
f=read_json(root/'frozen_config.json');task_path=scene/'tasks/pick-bf2b057e9916';task=read_json(task_path/'task.json')
trials=read_json(task_path/'trials.json');winning=next(t for t in trials if t['status']=='success')
stations=read_json(task_path/'stations.json');goal=next(s for s in stations if s['station_id']=='S0032')  # explicit nav-feasible smoke goal, NOT empirical-best claim
with np.load(task_path/'navigation_grid.npz') as z: grid=Grid(z['origin'],float(z['resolution_m']),z['free'])
choices=[]
for s in stations:
 if s['geometry']=='valid' and s['station_id']!=goal['station_id']:
  p=search(grid,s['xy'],goal['xy'])
  if p: choices.append((p['length_m'],s,p))
if not choices: raise RuntimeError('No distinct reachable S0 for continuous smoke')
_,start,path=min(choices,key=lambda v:(v[0],v[1]['station_id']))
print('Start',start['station_id'],'goal',goal['station_id'],'path',path['length_m'],flush=True)
sim=Simulation.from_snapshot(scene/'scene',RobotConfig(**f['robot']))
try:
 config=NoEditConfig(**f['config']);collection=CollectionConfig(**f['collection']);candidates=grasp_candidates(task,f['assets_dir'])
 result=run_raw_attempt(sim,task,start,candidates,f['assets_dir'],config,collection,scene,'four-view-continuous-smoke-'+uuid.uuid4().hex[:8],seed=winning['control']['seed'],path=path,goal=goal,winning_control=winning['control'])
 write_json(root/'continuous_smoke.json',{'scope':'real continuous smoke, incomplete station statistics, not final dataset', 'gpu':gpu,'s0':start['station_id'],'s1':goal['station_id'],'path':path,**result})
 print(json.dumps(result,ensure_ascii=False),flush=True)
finally:sim.close()
