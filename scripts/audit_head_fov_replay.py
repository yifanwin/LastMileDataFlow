"""Retrospective measured-replay FOV audit, not the planner's sparse validator."""
import argparse
from pathlib import Path
import mujoco
import numpy as np
from lastmile_dataflow.config import RobotConfig, construct
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.planning.head_fov import camera_definition


def audit(sim, attempt, definition):
    data = mujoco.MjData(sim.model)
    cid = definition['camera_id']; rows = []
    with np.load(attempt/'replay.npz') as replay:
        for time, qpos, phase in zip(replay['times'], replay['qpos'], replay['phases']):
            data.qpos[:] = qpos; mujoco.mj_forward(sim.model, data)
            local = data.cam_xmat[cid].reshape(3,3).T @ (data.xpos[sim.target_id]-data.cam_xpos[cid])
            depth = -float(local[2])
            limits = depth*np.array([definition['tan_half_x'],definition['tan_half_y']])
            edge = float(np.max(np.abs(local[:2])/limits)) if depth > .01 else None
            rows.append({'time_s':float(time),'phase':str(phase),'edge_fraction':edge,
                         'depth_m':depth, 'valid':bool(depth > .01 and np.all(np.abs(local[:2]) < limits))})
    invalid = [r for r in rows if not r['valid']]
    return {'attempt':str(attempt), 'outcome':read_json(attempt/'outcome.json'), 'frames':len(rows),
            'invalid_frames':len(invalid),'behind_or_too_close_frames':sum(r['depth_m']<=.01 for r in rows),
            'max_edge_fraction':max((r['edge_fraction'] for r in rows if r['edge_fraction'] is not None),default=None),
            'first_invalid':invalid[0] if invalid else None,
            'phase_max_edge':{phase:max((r['edge_fraction'] for r in rows if r['phase']==phase and r['edge_fraction'] is not None),default=None)
                              for phase in dict.fromkeys(r['phase'] for r in rows)},
            'scope':'measured target body origin, geometric frustum only; no occlusion claim'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--frozen-run',required=True,type=Path)
    parser.add_argument('--attempt',action='append',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    frozen=read_json(args.frozen_run/'frozen_config.json')
    scene=args.frozen_run/'scenes/val_103';task=read_json(next((scene/'tasks').glob('pick-*'))/'task.json')
    sim=Simulation.from_snapshot(scene/'scene',construct(RobotConfig,frozen['robot']),target=task['target_body'])
    try:
        definition=camera_definition(sim,frozen['config']['width'],frozen['config']['height'])
        write_json(args.output,{'camera':definition,'audits':[audit(sim,p,definition) for p in args.attempt]})
    finally:sim.close()

if __name__=='__main__':main()
