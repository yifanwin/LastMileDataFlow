"""成功示教视图、按本体布局独立物化与真实控制窗口读取。"""
from collections import Counter
from pathlib import Path
import shutil

import imageio.v2 as imageio
import numpy as np

from ...utils.io import create_run, read_json, rows, write_json, write_rows


def successful(record, *, continuous=False, observations=False):
    valid = (record.get('status') == 'success'
             and (record.get('task_outcome') or {}).get('status') == 'success'
             and record.get('recording_complete') is True
             and (record.get('executed_steps') or 0) > 0
             and bool(record.get('rollout_id')))
    return (valid and (not continuous or (record.get('navigation') or {}).get('status') == 'success')
            and (not observations or record.get('observation_complete') is True))


def training_record(record, source_run, source_type):
    fields = ('scene_id', 'task_id', 'station_id', 'trial', 'seed', 'rollout_id',
              'status', 'task_outcome', 'recording_complete', 'observation_complete', 'executed_steps')
    result = {key: record[key] for key in fields if key in record}
    result.update(source_run=source_run, source_type=source_type)
    result.update({key: record[key] for key in ('labels', 's1_tiers', 'diagnosis_ref', 'dataset_split',
                                             'observations_ref', 'observation_source') if key in record})
    if source_type == 's0_to_s1_continuous':
        result.update({key: record[key] for key in ('pair_id', 's0', 's1', 'navigation',
                       'euclidean_distance_m', 'astar_path_length_m', 'navigation_steps')})
    return result


def _source(root, record):
    return (root / record['source_run']).resolve(), record['rollout_id']


def _copy_rollout(source, target, config, record):
    """只复制训练必需文件；外部源目录始终只读。"""
    recording = read_json(source / 'recording.json')
    if not recording['complete'] or recording['steps'] <= 0:
        raise ValueError(f'incomplete or empty recording: {source}')
    with np.load(source / 'trajectory.npz', allow_pickle=False) as trace:
        n = recording['steps']
        layout = recording.get('layout') or {'state_key':'state22', 'action_size':20, 'state_size':22}
        state_key = layout['state_key']
        action_size = layout.get('action_size', len(layout.get('action_fields', [])))
        state_size = layout.get('state_size', len(layout.get('state_fields', [])))
        if (trace['actions'].shape != (n, action_size) or trace[state_key].shape != (n + 1, state_size)
                or len(trace['phases']) != n or len(trace['time_s']) != n + 1):
            raise ValueError(f'trajectory boundaries do not match recording: {source}')
    target.mkdir(parents=True)
    for filename in ('trajectory.npz', 'recording.json'):
        shutil.copyfile(source / filename, target / filename)
    collection = config['collection']
    observation_root = Path(record['observations_ref']) if record.get('observations_ref') else source
    replayed = read_json(observation_root / 'observations.json') if record.get('observations_ref') else None
    if replayed and (not replayed['complete'] or replayed['frames'] != n + 1):
        raise ValueError('incomplete replayed training observations')
    cameras = (replayed['cameras'] if replayed else
               config['profiles'][record['scene_id']]['robot']['cameras'] if collection['record_images'] else [])
    record_depth = replayed['record_depth'] if replayed else collection['record_depth']
    for camera in cameras:
        destination = target / 'images' / camera
        destination.mkdir(parents=True)
        for step in range(n + 1):
            names = [f'{step:06d}.png']
            if record_depth:
                names.append(f'{step:06d}-depth.npy')
            for name in names:
                shutil.copyfile(observation_root / 'images' / camera / name, destination / name)
    # 原配置是来源说明；读取器只消费下面的相机与录制信息，不加载外部资产。
    write_json(target / 'metadata.json', {'cameras': cameras, 'record_depth': record_depth,
               'execution': config['config'], 'collection': collection,
               'profile': config['profiles'][record['scene_id']],
               'action_layout': layout.get('action_layout', 'rby1-20d'), 'state_layout': layout.get('state_layout', 'state22'),
               'state_key': state_key, 'layout': layout})


def materialize_lastmile(dataset, output):
    """独立 CPU 后处理；输入引用型数据集，输出新的自包含训练数据集。"""
    dataset = Path(dataset).resolve()
    for name in ('config.json', 'train_index.jsonl', 'pair_index.jsonl'):
        if not (dataset / name).is_file():
            raise FileNotFoundError(dataset / name)
    source_config = read_json(dataset / 'config.json')
    if source_config.get('kind') == 'materialized_lastmile':
        raise ValueError('materialization expects a reference dataset; copy an already materialized dataset directly')
    training, pairs = rows(dataset / 'train_index.jsonl'), rows(dataset / 'pair_index.jsonl')
    output = Path(output).resolve()
    if output.is_relative_to(dataset):
        raise ValueError('materialization output must be outside the input dataset')
    root = create_run(output, {'kind': 'materialized_lastmile', 'source': str(dataset),
                               'action_layout': 'per_rollout', 'source_config': source_config})
    copied, views, configs = {}, [], {}
    complete = False
    try:
        for record in training:
            continuous = record['source_type'] == 's0_to_s1_continuous'
            if record['source_type'] not in ('s1_direct_operation', 's0_to_s1_continuous') or not successful(record, continuous=continuous):
                raise ValueError('train_index contains a non-successful demonstration')
            source_run, rollout_id = identity = _source(dataset, record)
            view = {key: value for key, value in record.items() if key not in ('source_run', 'rollout_id')}
            if identity in copied:
                previous = views[copied[identity][1]]
                # 保留不同的关联，不因重复引用复制轨迹或扩增样本。
                if view != {key: value for key, value in previous.items()
                            if key not in ('source', 'rollout_path', 'references')}:
                    previous.setdefault('references', []).append(view)
                continue
            relative = f'rollouts/d{len(copied):06d}'
            if source_run not in configs:
                configs[source_run] = read_json(source_run / 'config.json')
            _copy_rollout(source_run / 'rollouts' / rollout_id, root / relative, configs[source_run], record)
            view.update(rollout_path=relative, source={'run': str(source_run), 'rollout_id': rollout_id})
            copied[identity] = (relative, len(views))
            views.append(view)
        for pair in pairs:
            rollout_id = pair.pop('rollout_id', None)
            source_run = pair.pop('source_run', '.')
            if rollout_id:
                identity = ((dataset / source_run).resolve(), rollout_id)
                pair['source'] = {'run': str(identity[0]), 'rollout_id': rollout_id}
                if identity in copied:
                    pair['rollout_path'] = copied[identity][0]
        write_rows(root / 'train_index.jsonl', views)
        write_rows(root / 'pair_index.jsonl', pairs)
        complete = True
    finally:
        write_json(root / 'summary.json', {'status': 'completed' if complete else 'incomplete',
                   'input_demonstrations': len(training), 'materialized_rollouts': len(copied),
                   'unique_source_attempts': len({_source(dataset, row) for row in training}),
                   'train_source_counts': dict(Counter(row['source_type'] for row in views)),
                   'planned_pairs': len(pairs), 'pair_status_counts': dict(Counter(p['status'] for p in pairs))})
    return root


def load_training_batch(dataset, indices, *, start=0, steps=16):
    """读取等长真实控制窗口；返回 NumPy batch，不做动作转换、补帧或 padding。"""
    if start < 0 or steps <= 0 or len(indices) == 0:
        raise ValueError('request a nonempty batch and a positive control window')
    root = Path(dataset).resolve()
    if read_json(root / 'summary.json')['status'] != 'completed':
        raise ValueError('materialization is incomplete')
    records = rows(root / 'train_index.jsonl')
    arrays, images, depths, tasks, sources = [], {}, {}, [], []
    camera_sets = []
    for index in indices:
        record = records[index]
        rollout = root / record['rollout_path']
        if not rollout.resolve().is_relative_to(root):
            raise ValueError('training path escapes materialized dataset')
        metadata = read_json(rollout / 'metadata.json')
        camera_sets.append((tuple(metadata['cameras']), metadata['record_depth'], metadata.get('state_layout', 'state22'), metadata.get('action_layout', 'rby1-20d')))
        with np.load(rollout / 'trajectory.npz', allow_pickle=False) as trace:
            if start + steps > len(trace['actions']):
                raise ValueError('requested window exceeds actual executed actions')
            arrays.append({key: trace[key][start:start + steps].copy()
                           for key in (metadata.get('state_key', 'state22'), 'actions', 'phases', 'time_s')})
        for camera in metadata['cameras']:
            directory = rollout / 'images' / camera
            images.setdefault(camera, []).append(np.stack([
                imageio.imread(directory / f'{step:06d}.png') for step in range(start, start + steps)]))
            if metadata['record_depth']:
                depths.setdefault(camera, []).append(np.stack([
                    np.load(directory / f'{step:06d}-depth.npy', allow_pickle=False)
                    for step in range(start, start + steps)]))
        tasks.append(read_json(rollout / 'recording.json')['task'])
        sources.append(record['source_type'])
    if len(set(camera_sets)) != 1:
        raise ValueError('batch observations have different camera/depth or robot layouts')
    return {**{key: np.stack([item[key] for item in arrays]) for key in arrays[0]},
            'images': {key: np.stack(value) for key, value in images.items()},
            'depth': {key: np.stack(value) for key, value in depths.items()},
            'tasks': tasks, 'source_types': sources}
