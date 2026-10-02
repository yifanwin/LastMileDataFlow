"""Explicit real GPU planner probe, fresh diagnostics only (no legacy task labels)."""
from pathlib import Path
from dataclasses import replace
import numpy as np
import mujoco
from lastmile_dataflow.config import load_config,RobotConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.stations.config import load_station_config
from lastmile_dataflow.stations.sampling import filter_station
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.planning.curobo import NativePlanner,load_grasps,body_pose
from lastmile_dataflow.io import write_json
from dataclasses import asdict

if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    robot=load_config(RobotConfig,root/'configs/robots/rby1.json')
    cfg=load_station_config(root/'configs/stations/case1-cup-probe.json')
    sim=Simulation.from_snapshot(root/'outputs/attempts/phase1-final-legacy/scene',robot,target=cfg.target)
    path=root/'outputs/planner_checks/case1-native-v1';path.mkdir(parents=True,exist_ok=False)
    print('filter',filter_station(sim,CollectionConfig()),flush=True)
    for n,q in zip(sim.robot.groups['torso'],[0,.738,-1.476,.738,0,0]): sim.data.qpos[sim.robot.addresses[n]]=q
    sim.robot.hold();mujoco.mj_forward(sim.model,sim.data)
    try:
        planner=NativePlanner(sim,cfg,'left',path)
        goal=body_pose(sim,sim.target_id)@load_grasps(cfg,sim)[794];goal[:3,3]-=goal[:3,2]*.08
        result=planner.plan(goal);print(result.status,result.diagnostics,flush=True);write_json(path/'result.json',asdict(result))
    finally:sim.close()
