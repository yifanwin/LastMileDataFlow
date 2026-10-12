from pathlib import Path
import json
import numpy as np
from lastmile_dataflow.io import read_json,write_json
from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.navigation.astar import scene_grid,task_support_obstacles,search
scene=Path('outputs/no_edit/no-edit-val-full-20261009/scenes/val_0').resolve()
robot=RobotConfig(**read_json(scene/'scene/version.json')['robot']);foot=read_json(scene/'robot_geometry.json')
choices=[]
for task_id in ('pick-5a86ecebea93','pick-ed4fde20087f'):
 tp=scene/'tasks'/task_id;task=read_json(tp/'task.json');stats=read_json(tp/'station_statistics.json');stations=read_json(tp/'stations.json')
 sim=Simulation.from_snapshot(scene/'scene',robot,target=task['target_body'])
 try:
  obstacles=task_support_obstacles(sim,task)
  grid=scene_grid(sim,task['anchor_world'],2.,.05,foot['radius_m'],.02,support_obstacles=obstacles)
 finally:sim.close()
 wins={r['station_id']:r for r in stats if r['successes']>0}
 valid=[s for s in stations if s['geometry']=='valid' and grid.cell(s['xy']) is not None and grid.free[grid.cell(s['xy'])]]
 goals=[s for s in valid if np.linalg.norm(np.asarray(s['xy'])-task['anchor_world'][:2])<=1.0]
 print(task_id,'support',obstacles,'valid',len(valid),'near_target_reachable_goals',[s['station_id'] for s in goals],flush=True)
 for goal in goals:
  gd=np.linalg.norm(np.asarray(goal['xy'])-task['anchor_world'][:2])
  for start in valid:
   sd=np.linalg.norm(np.asarray(start['xy'])-task['anchor_world'][:2])
   if sd<=gd+.4:continue
   path=search(grid,start['xy'],goal['xy'])
   if path and .8<=path['length_m']<=1.8:
    choices.append((float(gd)+.1*abs(path['length_m']-1.2),{'task_dir':str(tp),'start_station':start['station_id'],'goal_station':goal['station_id'],
      'goal_successes':wins.get(goal['station_id'],{}).get('successes',0),'s0_target_distance_m':float(sd),'s1_target_distance_m':float(gd),'path_length_m':path['length_m'],'support_obstacles':obstacles}))
if not choices:raise RuntimeError('no near-target reachable station in other scenes')
_,selected=min(choices,key=lambda item:item[0]);print('SELECTED',selected,flush=True)
write_json(Path('/tmp/selected-other-astar.json'),selected)
