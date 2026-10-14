"""LastMile 入口只选择实验目的，不建立父子 run 或派生发布。"""
from ..config import CollectionConfig
from ..stations.no_edit_config import load_no_edit_config
from .no_edit import collect_scene_dataset


def collect(dataset, assets_dir, config_path, output_dir, *, run_id, gpu_ids, workers=1,
            samples=None, resume=False, calibration_only=False, max_trials=None, calibration=None, collection=None):
    if workers != 1 or resume:
        raise ValueError('use a fresh run with one worker')
    config = load_no_edit_config(config_path)
    collection = collection or CollectionConfig(output_dir=str(output_dir), seed=config.seed, width=config.width,
                                  height=config.height, record_video=False)
    return collect_scene_dataset(dataset, assets_dir, config, collection, output_dir, run_id=run_id,
        gpu_ids=gpu_ids, samples=samples, continuous=not calibration_only,
        calibration=calibration, max_trials=max_trials)
