"""Minimal LastMileBench build and one-policy evaluation, outside the old pipeline."""
from dataclasses import asdict
import os
from pathlib import Path
import time
import traceback

import numpy as np

from ..benchmark.episodes import episode_id, select_starts, summarize
from ..config import CollectionConfig, construct, RobotConfig
from ..io import canonical, digest, file_digest, read_json, write_json
from ..policies.molmobot_config import load_policy_config, policy_manifest
from ..runtime.simulation import Simulation
from ..runtime.benchmark_scene import native_scene
from ..runtime.no_edit_execution import clone_sim
from ..stations.no_edit_config import NoEditConfig
from ..stations.no_edit_sampling import place_frozen_station, robot_contacts
from ..stations.scene_package import package_manifest, robot_descriptor
from ..scenes.initialization import floor_support
from ..recording.head_views import head_frame
from ..recording.edit_views import ViewConfig
from .no_edit import sustained_policy_gpus


SCHEMA = 'lastmilebench-v0'


def gpu_environment(ids, policy_path):
    config = load_policy_config(policy_path)
    candidates = sustained_policy_gpus(ids, config)
    if not candidates:
        raise RuntimeError('no sustained low-utilization GPU with sufficient free VRAM')
    chosen = candidates[0]
    os.environ.update(CUDA_VISIBLE_DEVICES=str(chosen['index']), MUJOCO_GL='egl',
                      MUJOCO_EGL_DEVICE_ID=str(chosen['index']))
    return {'chosen':chosen, 'eligible':candidates}


def verify(path):
    root = Path(path).resolve()
    checksums = read_json(root/'checksums.json')
    if 'manifest.json' not in checksums or 'episodes.json' not in checksums:
        raise ValueError('incomplete benchmark seal')
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p.name != 'checksums.json'}
    # Nested scene/checksums.json is itself a bound artifact.
    actual |= {str(p.relative_to(root)) for p in (root/'samples').rglob('checksums.json')}
    if actual != set(checksums):
        raise ValueError('benchmark seal file-set mismatch')
    for name, sha in checksums.items():
        file = (root/name).resolve()
        if not file.is_relative_to(root) or not file.is_file() or file_digest(file)!=sha:
            raise ValueError('benchmark input modified: '+name)
    manifest = read_json(root/'manifest.json')
    if manifest['schema_version']!=SCHEMA or manifest['status']!='sealed':
        raise ValueError('unsupported/unsealed benchmark')
    episodes = read_json(root/'episodes.json')
    if len(episodes)!=manifest['episode_count'] or not episodes:
        raise ValueError('invalid benchmark episode count')
    summarize(episodes, [])
    for episode in episodes:
        sample = episode['sample_id']
        if sample not in manifest['samples']:
            raise ValueError('unknown sample binding')
        item = manifest['samples'][sample]
        for key in ('scene_dataset_id','robot_sensor_config_id','task_id'):
            if episode[key]!=item[key]:
                raise ValueError('episode binding mismatch')
        station = episode['station']
        if station['station_id']!=episode['start_station_id'] or episode_id({k:v for k,v in episode.items() if k!='episode_id'})!=episode['episode_id']:
            raise ValueError('episode identity mismatch')
    return manifest, episodes


def build(dataset, output, policy_path, *, gpu_ids, starts_per_sample=1,
          calibration_dir=None, allow_uncalibrated=False, seed=20261012,
          horizon=400, wall_timeout_s=600., radius_m=1.5):
    if type(horizon) is not int or horizon<=0 or not np.isfinite([wall_timeout_s,radius_m]).all() or min(wall_timeout_s,radius_m)<=0:
        raise ValueError('invalid benchmark budget')
    dataset, output = Path(dataset).resolve(), Path(output).resolve()
    source = read_json(dataset/'manifest.json')
    if source.get('status')!='complete' or source.get('schema_version')!='scene-datasets-research-v2':
        raise ValueError('LastMileBench requires complete research scene_datasets')
    if calibration_dir is None and not allow_uncalibrated:
        raise ValueError('provide calibration, or explicitly enable uncalibrated smoke')
    packages = sorted((dataset/'samples').glob('*/robot_sensor_configs/*/manifest.json'))
    if not packages:
        raise ValueError('no scene packages')
    # All verification happens before allocating a GPU or creating an output.
    manifests = [package_manifest(p.parent) for p in packages]
    gpu = gpu_environment(gpu_ids, policy_path)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output/'build_gpu.json', gpu)
    episodes, samples, hidden = [], {}, {}
    try:
        for package, manifest in zip(packages, manifests):
            package = package.parent
            sample_id = manifest['sample_id']
            if sample_id in samples:
                raise ValueError('v0 supports one robot configuration per sample')
            task = read_json(package/'task.json')
            old_robot = construct(RobotConfig, read_json(package/'scene/version.json')['robot'])
            parent = Simulation.from_snapshot(package/'scene',old_robot,target=task['target_body'])
            native = None
            try:
                native = native_scene(parent)
                sample_root = output/'samples'/sample_id
                native.freeze(sample_root/'scene')
                binding = {'scene_dataset_id':source['scene_dataset_id'], 'sample_id':sample_id,
                    'source_package_identity':manifest['package_identity'],
                    'robot_sensor_config_id':'rs-'+digest({'robot':robot_descriptor(native.robot.config),
                        'cameras':{'width':1024,'height':576,'head_fovy':139.,'wrist_fovy':58.}}),
                    'compiled_model_sha256':native.model_hash, 'task_id':task['task_id']}
                write_json(sample_root/'robot.json',asdict(native.robot.config))
                write_json(sample_root/'task.json',task)
                write_json(sample_root/'binding.json',binding)
                samples[sample_id] = {**binding, 'source_robot_sensor_config_id':manifest['robot_sensor_config_id'],
                    'source_package':str(package), 'scene_regenerated':False}
                calibration = read_json(Path(calibration_dir)/(sample_id+'.json')) if calibration_dir else None
                station_data = read_json(package / 'stations.json')
                stations = station_data['validated'] if calibration else [s for s in station_data['validated'] if s['station_id'] in station_data['selected_ids']]
                choices = select_starts(stations,calibration,binding,limit=starts_per_sample)
                if not choices:
                    raise ValueError('no eligible low/high nearby episode in '+sample_id)
                for station, evidence in choices:
                    probe = clone_sim(native,target=task['target_body'])
                    try:
                        place_frozen_station(probe, station)
                        collisions = robot_contacts(probe)
                        ground = floor_support(probe, station['xy'])
                        _,_,visibility = head_frame(probe,task['target_body'],ViewConfig(width=1024,height=576))
                        distance = float(np.linalg.norm(np.asarray(station['xy'])-task['anchor_world'][:2]))
                        valid = bool(ground and not collisions and visibility['visible_pixels']>=24
                            and distance<=radius_m and np.isfinite(probe.data.qpos).all()
                            and not any(w.number for w in probe.data.warning))
                        check = {'valid':valid,'ground':ground,'collisions':collisions,
                                 'visible_pixels':visibility['visible_pixels'],'target_distance_m':distance,
                                 'exact_frozen_station_pose':True}
                        if not valid:
                            raise ValueError('native initialization check failed: '+station['station_id'])
                    finally:
                        probe.close()
                    entry = {'scene_dataset_id':source['scene_dataset_id'], 'sample_id':sample_id,
                        'robot_sensor_config_id':binding['robot_sensor_config_id'], 'task_id':task['task_id'],
                        'start_station_id':station['station_id'], 'station':station, 'case':manifest['case'],
                        'seed':seed, 'eligibility':evidence['status'], 'initialization_check':check}
                    entry['episode_id'] = episode_id(entry)
                    episodes.append(entry)
                    hidden[entry['episode_id']] = evidence
                if calibration:
                    write_json(sample_root/'calibration.json',calibration)
            finally:
                if native: native.close()
                parent.close()
        write_json(output/'episodes.json',episodes)
        write_json(output/'calibration_evidence.json',hidden)
        write_json(output/'manifest.json',{'schema_version':SCHEMA,'status':'sealed',
            'scene_dataset_id':source['scene_dataset_id'], 'source_manifest_sha256':file_digest(dataset/'manifest.json'),
            'samples':samples,'episode_count':len(episodes),'policy_horizon':horizon,
            'wall_timeout_s':wall_timeout_s,'workspace_radius_m':radius_m,
            'action_protocol':'rby1-multitask-molmobot-native-v1',
            'success_protocol':'mobile-vla-physical-v2','calibrated':calibration_dir is not None,
            'difficulty':'not_estimated','path_validation':'not_required_v0'})
        write_json(output/'checksums.json',{str(p.relative_to(output)):file_digest(p)
            for p in sorted(output.rglob('*')) if p.is_file()})
        verify(output)
    except BaseException:
        write_json(output/'build_failure.json',{'status':'unsealed','traceback':traceback.format_exc()})
        raise
    return output


def evaluate(benchmark, output, policy_path, *, gpu_ids, log_dir):
    from ..policies.molmobot import MolmoBotClient
    from ..runtime.molmobot_execution import run_vla_attempt
    benchmark, output = Path(benchmark).resolve(), Path(output).resolve()
    manifest, episodes = verify(benchmark)
    gpu = gpu_environment(gpu_ids,policy_path)
    output.mkdir(parents=True,exist_ok=False)
    from ..recording.protocol import code_identity
    write_json(output/'code_identity.json', code_identity())
    logs = Path(log_dir).resolve(); logs.mkdir(parents=True,exist_ok=True)
    policy_config = asdict(load_policy_config(policy_path))
    policy_config['task_horizon'] = manifest['policy_horizon']
    write_json(output/'policy.json',policy_config)
    write_json(output/'run.json',{'benchmark':str(benchmark),'benchmark_sha256':file_digest(benchmark/'checksums.json'),
        'gpu':gpu,'strategy':'molmobot','started_unix':time.time(), 'policy_manifest':policy_manifest(load_policy_config(output/'policy.json'))})
    results, policy = [], None
    try:
        policy = MolmoBotClient(output/'policy.json',logs/(output.name+'-model.log'))
        write_json(output/'model_service.json',policy.metadata)
        for episode in episodes:
            sample = benchmark/'samples'/episode['sample_id']
            root = output/'episodes'/episode['episode_id']; root.mkdir(parents=True,exist_ok=False)
            (root/'scene').symlink_to(sample/'scene',target_is_directory=True)
            sim = Simulation.from_snapshot(sample/'scene',construct(RobotConfig,read_json(sample/'robot.json')),
                                           target=read_json(sample/'task.json')['target_body'])
            try:
                config = NoEditConfig(operation_backend='molmobot',operation_base_mode='policy_relative',
                    policy_config=str(output/'policy.json'),width=1024,height=576,
                    radius_m=manifest['workspace_radius_m'],attempt_timeout_s=manifest['wall_timeout_s'],
                    third_person_enabled=False)
                collection = CollectionConfig(output_dir=str(output),width=1024,height=576,
                    seed=episode['seed'],max_steps=manifest['policy_horizon'],record_depth=False)
                result = run_vla_attempt(sim,read_json(sample/'task.json'),episode['station'],config,
                    collection,root,episode['episode_id'],seed=episode['seed'],policy=policy)
                result['episode_id'] = episode['episode_id']
                replay_path = Path(result['attempt'])/'replay.npz'
                if replay_path.is_file() and result.get('executed_steps'):
                    with np.load(replay_path, allow_pickle=False) as replay:
                        positions = replay['qpos'][:,[sim.robot.addresses[n] for n in sim.robot.groups['base']]][:,:2]
                        result['base_displacement_m'] = float(np.linalg.norm(positions[-1]-positions[0]))
                        result['base_path_length_m'] = float(np.linalg.norm(np.diff(positions,axis=0),axis=1).sum())
                        result['elapsed_sim_s'] = float(replay['time_s'][-1]-replay['time_s'][0])
                        result['base_path_measurement'] = 'actual policy-boundary XY samples'

                results.append(result)
                with (output/'results.jsonl').open('ab') as stream:
                    stream.write(canonical(result)+b'\n'); stream.flush(); os.fsync(stream.fileno())
                write_json(output/'summary.json',summarize(episodes,results))
                print(canonical({'episode':episode['episode_id'],'status':result['status'],
                    'reason':result['reason'],'steps':result.get('executed_steps')}).decode(),flush=True)
                if result.get('failure_scope')=='shared':
                    break
            finally:
                sim.close()
    except Exception:
        write_json(output/'run_failure.json',{'status':'infrastructure_error','traceback':traceback.format_exc()})
        raise
    finally:
        if policy: policy.close()
        write_json(output/'summary.json',summarize(episodes,results))
    return output
