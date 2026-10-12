from pathlib import Path
import json
import numpy as np
from lastmile_dataflow.io import read_json
from lastmile_dataflow.config import RobotConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.scenes.geometry import body_points
from lastmile_dataflow.navigation.astar import scene_grid,task_support_obstacles
root=Path('outputs/diagnostics/astar-navigation-video-val103-20261010-3def3fdf')
req=read_json(root/'navigation_request.json');plan=read_json(root/'test_plan.json');snap=Path(plan['source_scene'])
robot=RobotConfig(**read_json(snap/'version.json')['robot']);sim=Simulation.from_snapshot(snap,robot,target=req['task']['target_body'])
try:
 target=body_points(sim,sim.target_id);center=(target.min(axis=0)+target.max(axis=0))/2
 obstacles=task_support_obstacles(sim,req['task'])
 foot=read_json(snap.parent/'robot_geometry.json')
 grid=scene_grid(sim,req['task']['anchor_world'],2.,.05,foot['radius_m'],.02,support_obstacles=obstacles)
 print('TARGET actual bbox center',center.tolist(),'task anchor',req['task']['anchor_world'])
 print('SUPPORT actual envelope',obstacles)
 for label in ('start','goal'):
  xy=np.asarray(req[label]['xy']);lo=np.asarray(obstacles[0]['min'])[:2];hi=np.asarray(obstacles[0]['max'])[:2]
  edge_distance=np.linalg.norm(np.maximum(np.maximum(lo-xy,xy-hi),0))
  print(label,req[label]['station_id'],'xy',xy.tolist(),'distance_to_target_m',np.linalg.norm(xy-center[:2]),'distance_to_support_boundary_m',edge_distance)
 stats=read_json(snap.parent/'tasks/pick-bf2b057e9916/station_statistics.json')
 for row in stats:
  if row['successes']>0:
   c=grid.cell(row['xy']);print('measured successful station',row['station_id'],'xy',row['xy'],'successes',row['successes'],'nav_free',bool(grid.free[c]) if c else False)
finally:sim.close()
rows=[json.loads(line) for line in (root/'attempts/navigation-only/trajectory.jsonl').read_text().splitlines()]
print('actual_initial_base',rows[0]['state_before']['robot']['base'])
print('actual_final_base',rows[-1]['state_after']['robot']['base'])
print('goal_has_existing_operation_success',req['goal_has_existing_operation_success'])
