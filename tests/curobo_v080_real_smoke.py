"""Explicit real-GPU smoke. Does not run under unittest discovery."""
import argparse
import copy
from datetime import datetime, timezone
from pathlib import Path
import shutil
import time
import numpy as np
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.config import RobotConfig, CollectionConfig, construct
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.no_edit_execution import run_raw_attempt, planner_options
from lastmile_dataflow.stations.no_edit_sampling import place_frozen_station
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.planning.curobo_v2 import V2Planner
from lastmile_dataflow.planning.curobo import tcp_pose
from lastmile_dataflow.runtime.no_edit_v2 import candidate_pool
from types import SimpleNamespace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--frozen-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--station', default='S0042')
    parser.add_argument('--mode', choices=('model','plan','execute','continuous'), default='execute')
    parser.add_argument('--goal-station', default='S0042')
    parser.add_argument('--winning-attempt', type=Path)
    parser.add_argument('--head-fov-weight', type=float)
    parser.add_argument('--head-fov', choices=('enabled','disabled'), default='enabled')
    parser.add_argument('--seed', type=int, default=20261010)
    args=parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    start=time.monotonic(); utc=datetime.now(timezone.utc).isoformat()
    frozen=read_json(args.frozen_run/'frozen_config.json')
    scene=args.frozen_run/'scenes/val_103'; task_dir=next((scene/'tasks').glob('pick-*'))
    task=read_json(task_dir/'task.json'); candidates=read_json(task_dir/'grasp_candidates.json')
    station=next(s for s in read_json(task_dir/'stations.json') if s['station_id']==args.station)
    overrides={'head_fov_enabled':args.head_fov=='enabled'}
    if args.head_fov_weight is not None:overrides['head_fov_weight']=args.head_fov_weight
    cfg=construct(NoEditConfig, {**frozen['config'], **overrides})
    robot=construct(RobotConfig,frozen['robot'])
    sim=Simulation.from_snapshot(scene/'scene',robot,target=task['target_body'])
    write_json(args.output/'experiment.json',{'question':'Does the pinned native 11D grasp pipeline complete all stages on the previously failing Cup station?',
        'baseline':'legacy S0042 approach planning failure after physical pregrasp',
        'expected':'native four planning queries or explicit bounded failure; success requires independent physical evidence',
        'success_gate':'real grip, unsupported lift and stable hold; full collection not approved by model-only pass',
        'station':station,'task':task,'mode':args.mode,'started_utc':utc,
        'head_fov':args.head_fov, 'head_fov_weight':cfg.head_fov_weight, 'planning_seed':args.seed})
    result=None
    try:
        if args.mode in ('execute','continuous'):
            shutil.copytree(scene/'scene',args.output/'scene')
            collection=construct(CollectionConfig,frozen['collection'])
            path=goal=control=None
            if args.mode=='continuous':
                from lastmile_dataflow.navigation.astar import Grid, search
                with np.load(task_dir/'navigation_grid.npz') as z:
                    grid=Grid(z['origin'],float(z['resolution_m']),z['free'])
                goal=next(s for s in read_json(task_dir/'stations.json') if s['station_id']==args.goal_station)
                if station['station_id']==goal['station_id']:raise ValueError('distinct S0/S1 required')
                path=search(grid,station['xy'],goal['xy'])
                if path is None:raise ValueError('no A* path')
                if args.winning_attempt:control=read_json(args.winning_attempt/'outcome.json')['control']
                write_json(args.output/'navigation_smoke.json',{'path':path,'goal':goal,
                    'scope':'continuous integration smoke; S0 is not claimed to be lowest-rate and this is not a final dataset record'})
            result=run_raw_attempt(sim,task,station,candidates,frozen['assets_dir'],cfg,collection,args.output,
                                   'v080-'+args.station+'-'+str(args.seed),seed=args.seed,path=path,goal=goal,winning_control=control)
        else:
            place_frozen_station(sim,station)
            pool=candidate_pool(SimpleNamespace(sim=sim,config=cfg),candidates,args.seed,None)
            queries=[]; results=[]
            def query(phase):
                if len(queries)>=12: raise RuntimeError('12-query budget')
                queries.append(phase)
            for side in ('left','right'):
                out=args.output/side;out.mkdir()
                planner=V2Planner(sim,planner_options(cfg,frozen['assets_dir'],args.seed),side,out,
                                  workspace=(task['anchor_world'],2.))
                if args.mode=='model':
                    rows=[]
                    for h in (0., .123, .369, .615, .738):
                        init=copy.deepcopy(station['initial']);init['torso']=[h];sim.robot.initialize(init)
                        actual=np.linalg.solve(planner.base,tcp_pose(sim,side))
                        fk=planner.motion.compute_kinematics(planner.state()).tool_poses.to_dict()[f'ee_{side}_tcp'].get_numpy_matrix().reshape(4,4)
                        err=float(np.linalg.norm(fk[:3,3]-actual[:3,3]))
                        angle=float(np.arccos(np.clip((np.trace(fk[:3,:3].T@actual[:3,:3])-1)/2,-1,1)))
                        if err > .001 or angle > .001:raise AssertionError((side,h,err,angle))
                        rows.append({'h':h,'translation_error_m':err,'rotation_error_rad':angle})
                    sim.robot.initialize(station['initial'])
                    results.append({'side':side,'names':list(planner.names),'tool_frames':planner.motion.tool_frames,
                                    'fk_height_grid':rows,'collision_sphere_count':planner.motion.kinematics.total_spheres})
                else:
                    from lastmile_dataflow.planning.curobo import body_pose
                    goals=[body_pose(sim,sim.model.body(c['body']).id)@c['pose_local'] for c in pool]
                    for g in goals:g[:3,3]+=g[:3,2]*cfg.approach_offset_m
                    success,index,stages,diagnostic=planner.grasp(goals,query)
                    results.append({'side':side,'success':success,'index':index,'diagnostic':diagnostic,
                                    'stage_points':{k:len(v) for k,v in stages.items()}})
                planner.close()
            result={'results':results,'query_phases':queries}
        write_json(args.output/'summary.json',result)
        print('SUMMARY',args.output/'summary.json',flush=True)
        if args.mode in ('execute','continuous'): print({k:result[k] for k in ('status','reason','executed_steps','video_status')},flush=True)
        else:print([{k:r[k] for k in ('side','success','stage_points') if k in r} for r in result['results']],flush=True)
    finally:
        sim.close()
        write_json(args.output/'timing.json',{'started_utc':utc,'finished_utc':datetime.now(timezone.utc).isoformat(),
                    'wall_time_s':time.monotonic()-start,'mode':args.mode,'summary_written':result is not None})

if __name__=='__main__':main()
