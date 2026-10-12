"""Five independent continuous navigation + MolmoBot trials on frozen sampled stations."""
import argparse
from dataclasses import asdict
from pathlib import Path
import shutil
import subprocess
import uuid
import numpy as np
from lastmile_dataflow.config import RobotConfig, CollectionConfig
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.io import read_json, write_json, file_digest
from lastmile_dataflow.navigation.astar import Grid, search
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.molmobot_execution import run_vla_attempt
from lastmile_dataflow.policies.molmobot import MolmoBotClient
from lastmile_dataflow.workflows.no_edit import task_seed


def main():
    p=argparse.ArgumentParser(); p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True); p.add_argument('--start',default='S0009')
    p.add_argument('--goal',default='S0023'); args=p.parse_args()
    source=args.source.resolve(); out=args.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    scene=source/'scenes/val_103'; taskdir=scene/'tasks/cup2'; frozen=read_json(source/'frozen_config.json')
    stations=read_json(taskdir/'stations.json'); stats=read_json(taskdir/'station_statistics.json')
    start=next(s for s in stations if s['station_id']==args.start.upper())
    goal=next(s for s in stations if s['station_id']==args.goal.upper())
    if start['geometry']!='valid' or goal['geometry']!='valid': raise ValueError('invalid stations')
    task=read_json(taskdir/'task.json'); config=NoEditConfig(**frozen['config'])
    robot=RobotConfig(**frozen['robot']); collection=CollectionConfig(**frozen['collection'])
    baseline=Simulation.from_snapshot(scene/'scene',robot)
    shutil.copytree(scene/'scene',out/'scene')
    griddata=np.load(taskdir/'navigation_grid.npz')
    path=search(Grid(griddata['origin'],float(griddata['resolution_m']),griddata['free']),start['xy'],goal['xy'])
    if not path:
        # The enclosing-circle grid rejects both valid stations: use a separately
        # verified short connector, never mark blocked grid cells as free.
        import copy
        from lastmile_dataflow.runtime.no_edit_execution import clone_sim
        from lastmile_dataflow.stations.no_edit_sampling import place_frozen_station, robot_contacts
        checks=[]
        xy=np.linspace(start['xy'],goal['xy'],101)
        yaw=np.linspace(start['base'][2],goal['base'][2],101)
        for point,angle in zip(xy,yaw):
            probe=clone_sim(baseline,target=task['target_body'])
            try:
                station=copy.deepcopy(start);station['initial']['base']=[*point,float(angle)]
                place_frozen_station(probe,station)
                collisions=robot_contacts(probe)
                checks.append({'base':station['initial']['base'],'collisions':collisions})
            finally: probe.close()
        write_json(out/'connector_collision_checks.json',checks)
        if any(c['collisions'] for c in checks): raise RuntimeError('planning_no_solution_exact_geometry')
        path={'xy':xy.tolist(),'length_m':float(np.linalg.norm(np.asarray(goal['xy'])-start['xy'])),
              'planner':'straight connector with 101 full robot static collision checks',
              'grid_status':'both station endpoints blocked by conservative enclosing-circle support inflation',
              'static_checks_only':True}

    write_json(out/'experiment.json',{'source':str(source),'source_config_sha256':file_digest(source/'frozen_config.json'),
        'base_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'hypothesis':'Navigation from S0009 to S0023 followed by MolmoBot yields more than 0/5 end-to-end successes.',
        'expected_successes':[1,5], 'trials':5,'start':start,'goal':goal,'path':path,
        'baseline':[s for s in stats if s['station_id'] in (start['station_id'],goal['station_id'])],
        'config':asdict(config),'collection':asdict(collection),
        'stop_criteria':'Five trials; infrastructure errors recorded separately; no teleport/reset after begin.',
        'retention':'All outcomes, actions, physics, replay, videos and frozen input retained in this output directory.'})
    logs=out.parents[1]/'logs'; logs.mkdir(exist_ok=True)
    results=[]; policy=None
    try:
        policy=MolmoBotClient(config.policy_config,logs/(out.name+'-model.log'))
        write_json(out/'model_service.json',policy.metadata)
        for i in range(5):
            seed=task_seed(config.seed,'val_103',task['task_id'],start['station_id']+'-'+goal['station_id'],i)
            attempt=f'transfer-{start["station_id"]}-{goal["station_id"]}-t{i}-{uuid.uuid4().hex[:8]}'
            print(f'Trial {i+1}/5 seed={seed}',flush=True)
            row=run_vla_attempt(baseline,task,start,config,collection,out,attempt,seed=seed,policy=policy,path=path,goal=goal)
            row['trial']=i+1;results.append(row);write_json(out/'trials.json',results)
            nav=sum(r['navigation']['status']=='success' for r in results)
            success=sum(r['status']=='success' and r['navigation']['status']=='success' for r in results)
            summary={'completed_trials':len(results),'requested_trials':5,'navigation_successes':nav,
                'end_to_end_successes':success,'end_to_end_success_rate':success/len(results),
                'navigation_success_rate':nav/len(results),'operation_success_rate_given_arrival':success/nav if nav else None,
                'statuses':[{'trial':r['trial'],'status':r['status'],'reason':r['reason'],'navigation':r['navigation']} for r in results]}
            write_json(out/'summary.json',summary); print(summary,flush=True)
    finally:
        if policy: policy.close()
        baseline.close()

if __name__=='__main__': main()
