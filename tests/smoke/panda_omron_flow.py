"""Real Panda station preparation; production CLI owns calibration and collection."""
import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path


def align_mobile(output, robot_config):
    import mujoco
    import numpy as np
    from copy import deepcopy
    from types import SimpleNamespace
    from curobo.types import DeviceCfg, JointState
    from curobo._src.robot.kinematics.kinematics import Kinematics
    from curobo._src.types.robot import RobotCfg
    from lastmile_dataflow.runtime.simulation import Simulation
    from lastmile_dataflow.planning.panda_omron import PlanningModel, TCP, configuration
    from lastmile_dataflow.scenes.geometry import tcp_pose, geom_points, body_pose
    from lastmile_dataflow.utils.io import write_json
    from panda_omron import floor_source, pivot_check, pose_errors
    output.mkdir(parents=True, exist_ok=False)
    sim = Simulation.from_source(floor_source(output), robot_config)
    rows = []
    rng = np.random.default_rng(42)
    device = DeviceCfg()
    try:
        pivot = pivot_check(sim)
        coverage = []
        spheres = configuration(sim.robot)['kinematics']['collision_spheres']
        for body, link, reference in [('mobilebase0_wheeled_base','omron_base','mobilebase0_base'),
                                      ('mobilebase0_fixed_support','omron_base','mobilebase0_base'),
                                      ('mobilebase0_support','panda_link0','robot0_link0')]:
            frame = body_pose(sim,sim.model.body(robot_config.namespace+reference).id)
            bid = sim.model.body(robot_config.namespace+body).id
            pts = []
            for g in range(sim.model.ngeom):
                if sim.model.geom_bodyid[g]==bid and (sim.model.geom_contype[g] or sim.model.geom_conaffinity[g]):
                    pts.extend((geom_points(sim,g)-frame[:3,3])@frame[:3,:3])
            cloud = np.array([v['center']+[v['radius']] for v in spheres[link]])
            gap = np.linalg.norm(np.array(pts)[:,None,:]-cloud[None,:,:3],axis=-1)-cloud[:,3]
            coverage.append({'body':body, 'vertices':len(pts),'outside':int((gap.min(axis=1)>.002).sum()),
                             'max_signed_distance_m':float(gap.min(axis=1).max())})
        for index,yaw in enumerate([0., np.pi/2, np.pi]):
            initial = deepcopy(robot_config.initial)
            initial['base_xyyaw'] = [1.2,-.7,yaw]
            sim.robot.initialize(initial)
            folder = output/f'frame-{index}'
            folder.mkdir()
            model = PlanningModel(sim,SimpleNamespace(operation_base_mode='holonomic_joint_planning',local_base_radius_m=.35),'right',folder,([1.2,-.7,0.],2.))
            kin = Kinematics(RobotCfg.create(model.cfg,device_cfg=device).kinematics)
            for _ in range(7 if index < 2 else 6):
                value = deepcopy(initial)
                value['base_xyyaw'] = (np.asarray(initial['base_xyyaw'])+rng.uniform(-.2,.2,3)).tolist()
                limits = [sim.model.joint(robot_config.namespace+n).range for n in sim.robot.groups['arm']]
                value['arm_q']['right'] = [float(rng.uniform(lo+.05,hi-.05)) for lo,hi in limits]
                sim.robot.initialize(value)
                js = JointState.from_position(device.to_device(model.current_joints()).view(1,-1),joint_names=list(model.names))
                fk = kin.compute_kinematics(js.reorder(kin.joint_names)).tool_poses.get_link_pose(TCP).get_numpy_matrix().reshape(4,4)
                err = pose_errors(tcp_pose(sim,'right'),model.base@fk)
                roundtrip = np.linalg.norm(model.base_target_world(model.current_joints())-sim.robot.base_pose())
                rows.append({**err,'base_roundtrip_error':float(roundtrip),'reference_yaw':yaw})
        valid = pivot['valid'] and all(r['outside']==0 for r in coverage) and all(r['position_m']<=.005 and r['rotation_rad']<=.01 and r['base_roundtrip_error']<1e-6 for r in rows)
        write_json(output/'result.json',{'valid':valid,'pivot':pivot,'collision_coverage':coverage,'rows':rows,'target_met':all(r['position_m']<=.002 and r['rotation_rad']<=.005 for r in rows)})
        print('mobile FK',valid, max(r['position_m'] for r in rows),max(r['rotation_rad'] for r in rows),flush=True)
    finally:
        sim.close()


def navigation(output, robot_config):
    from dataclasses import asdict
    import numpy as np
    from lastmile_dataflow.config import CollectionConfig
    from lastmile_dataflow.runtime.simulation import Simulation
    from lastmile_dataflow.scenes.loading.source import SceneSource
    from lastmile_dataflow.runtime.execution_config import ExecutionConfig
    from lastmile_dataflow.data.recorder import AttemptRecorder
    from lastmile_dataflow.runtime.execution.context import ContinuousContext
    from lastmile_dataflow.runtime.execution.navigation import navigate
    from lastmile_dataflow.scenes.tasks.discovery import robot_footprint
    from lastmile_dataflow.planning.astar import scene_grid, search
    from lastmile_dataflow.utils.io import write_json
    output.mkdir(parents=True, exist_ok=False)
    scene = output/'navigation.xml'
    scene.write_text('<mujoco><worldbody><light pos="0 0 5"/><geom name="floor" type="plane" size="5 5 .1"/>'
        '<body name="obstacle" pos="1 0 .65"><geom type="box" size=".2 .25 .65"/></body>'
        '<body name="target" pos="4 4 .1"><geom type="sphere" size=".05"/></body></worldbody></mujoco>')
    sim = Simulation.from_source(SceneSource('navigation',str(scene.resolve())), robot_config, target='target')
    config = ExecutionConfig(head_fov_enabled=False, third_person_enabled=False, attempt_timeout_s=300.)
    collection = CollectionConfig(record_images=False, record_video=False, record_physics=True)
    recorder = AttemptRecorder(output, {'collection':asdict(collection), 'robot':asdict(robot_config)},attempt_id='navigation')
    recorder.bind_simulation(sim)
    ctx = ContinuousContext(sim, recorder, config)
    initial_arm = sim.robot.arm_positions('right').copy()
    try:
        grid = scene_grid(sim, [1.,0.,0.], 2., .05, robot_footprint(sim,whole_body=True)['radius_m'], .04, robot_height=robot_footprint(sim,whole_body=True)['max'][2])
        path = search(grid, [0.,0.], [2.,0.])
        if path is None:
            raise ValueError('navigation validation has no A* path')
        result = navigate(ctx,path,{'base':[2.,0.,1.5707963267948966]})
        with_arm = float(np.max(np.abs(sim.robot.arm_positions('right')-initial_arm)))
        result.update(path=path, arm_error_rad=with_arm,
                      lift_error_m=abs(sim.robot.group('lift')[0]-sim.robot.lift_target),
                      contacts=ctx.contact_stats.metrics())
        result['valid'] = result['status']=='success' and with_arm < .02 and result['lift_error_m']<.002 and result['contacts']['forbidden_contact_count']==0
        write_json(output/'result.json',result)
        recorder.finish('success' if result['valid'] else 'failure','navigation_validation')
        print(result['valid'], result['steps'], result['xy_error_m'], flush=True)
    except Exception as error:
        write_json(output/'result.json', {'valid':False,'reason':str(error),'contacts':ctx.contact_stats.metrics()})
        recorder.finish('failure',str(error))
        raise
    finally:
        ctx.finish()
        recorder.abort_close()
        sim.close()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--stage', choices=['prepare','navigation','align-mobile'], default='prepare')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gpu', type=int, required=True)
    parser.add_argument('--robot', default='configs/robots/panda_omron_mobile.json')
    parser.add_argument('--case', type=Path, default=Path('configs/execution/panda_omron_fixed_pick.json'))
    parser.add_argument('--spacing', type=float)
    parser.add_argument('--max-stations', type=int, default=20, help='uniform coverage limit; 0 keeps all physically valid stations')
    parser.add_argument('--radius', type=float)
    args = parser.parse_args()
    os.environ.update(CUDA_VISIBLE_DEVICES=str(args.gpu), MUJOCO_EGL_DEVICE_ID=str(args.gpu), MUJOCO_GL='egl')
    from lastmile_dataflow.config import load_robot_config, read_config
    from lastmile_dataflow.runtime.simulation import Simulation
    from lastmile_dataflow.scenes.loading.source import SceneSource
    from lastmile_dataflow.scenes.tasks.discovery import discover_tasks
    from lastmile_dataflow.scenes.stations.sampling import sample_stations
    from lastmile_dataflow.scenes.dataset_config import SceneStationConfig
    from lastmile_dataflow.utils.io import write_json, write_rows
    if args.stage == 'align-mobile':
        return align_mobile(args.output, load_robot_config(args.robot))
    if args.stage == 'navigation':
        return navigation(args.output, load_robot_config(args.robot))
    case = read_config(args.case, data_fields=('scene_xml','metadata','assets_dir'))
    objects = json.loads(Path(case['metadata']).read_text())['objects']
    target = next(name for name, row in objects.items() if row['asset_id']==case['target_asset'] and row['object_id']==case['source_object_id'])
    config = load_robot_config(args.robot)
    initial = {**config.initial, 'base_xyyaw':case['base_xyyaw']}
    config = replace(config, initial=initial)
    source = SceneSource('val_103', case['scene_xml'], case['metadata'])
    overrides = {key:value for key,value in [('radius_m',args.radius),('spacing_m',args.spacing)] if value is not None}
    width,height = (256,256) if config.control_profile=='robocasa-v1' else (320,240)
    geometry = SceneStationConfig.from_robot(config, **overrides, operations=['pick'], gripper_width_m=.08,
        max_pick_object_size_m=.20, navigation_whole_body=True, width=width, height=height)
    args.output.mkdir(parents=True, exist_ok=False)
    sim = Simulation.from_source(source, config, target=target)
    try:
        tasks, skipped = discover_tasks(sim, objects, geometry)
        task = next(t for t in tasks if t['target_body']==target and t['operation']=='pick')
        stations = sample_stations(sim, task, geometry)
        valid = [s for s in stations if s['geometry']=='valid']
        from lastmile_dataflow.scenes.dataset import select_stations
        selected, selection = select_stations(stations, task['anchor_world'],
            method='coverage' if args.max_stations else 'all_valid', limit=args.max_stations)
        print('stations', len(stations), 'valid', len(valid), 'selected', len(selected), flush=True)
        sim.freeze(args.output/'scene')
        write_json(args.output/'config.json', {'geometry':asdict(geometry)})
        write_rows(args.output/'scenes.jsonl', [{'scene_id':'panda-val103-cup', 'scene':'scene', 'status':'accepted',
            'task':task, 'stations':selected}])
        write_json(args.output/'sampling.json', {'stations':stations, 'valid':len(valid), 'total':len(stations), 'selection':selection})
    finally:
        sim.close()


if __name__=='__main__':
    main()
