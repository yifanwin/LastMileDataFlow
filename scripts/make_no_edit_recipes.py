"""Generate the no-edit v2 recipes for the pilot and ten 100-house batches."""
from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'configs/scene_datasets/no_edit_val'

TEMPLATE = {
    'schema_version': 'scene-dataset-recipe-v2',
    'data_root': '/data1/wenyifan/lmdf/molmospaces_data',
    'dataset_dir': 'assets/scenes/procthor-10k-val',
    'assets_dir': 'assets',
    'asset_index': '/data1/wenyifan/lmdf/LastMileDataFlow/outputs/asset_index/task-modes-thor-20261025-r02/assets.jsonl',
    'robot_config': 'configs/robots/panda_omron_robocasa.json',
    'mode_config': 'configs/task_modes/mode_defaults.json',
    'execution_config': 'configs/execution/panda_omron_no_edit_curobo.json',
    'operations': ['pick', 'open'],
    'modes': [],
    'exclude_categories': [],
    'include_categories': [],
    'include_assets': {},
    'exclude_tasks': [],
    'variants_per_intent': 1,
    'max_final_stations': 20,
    'max_pick_targets_per_house': -1,
    'max_open_targets_per_house': -1,
    'station_config': {
        'radius_m': 1.0,
        'spacing_m': 0.25,
        'width': 256,
        'height': 256,
        'gripper_width_m': 0.08,
        'max_pick_object_size_m': 0.2,
        'navigation_whole_body': True,
    },
    'split_seed': 42,
    'train_fraction': 0.0,
    'seed': 42,
    'task_timeout_s': 300.0,
    'local_task_models': False,
    'settle': {'require_source_stability': False},
}


def write(name, first, last=None):
    last = first if last is None else last
    value = {**TEMPLATE, 'houses': list(range(first, last + 1))}
    path = OUT / name
    path.write_text(json.dumps(value, indent=2) + '\n')
    print(f'{path.relative_to(ROOT)} houses={first}-{last}')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    write('pilot_000_004.json', 0, 4)
    write('all_000_999.json', 0, 999)
    write('remaining_005_999.json', 5, 999)
    for first in range(0, 1000, 100):
        write(f'batch_{first:03d}_{first + 99:03d}.json', first, first + 99)


if __name__ == '__main__':
    main()
