"""Rebuild augmented scenes under the native MolmoBot robot profile.

`augment-obstacles` freezes every variant with the robot profile of its baseline
snapshot (position-v1/20Hz). `collect-no-edit` forces the MolmoBot profile
(molmobot-native-v1/10Hz) and `Simulation.from_snapshot` rejects a mismatched
robot, so those frozen variants cannot be consumed by the VLA backend.

This tool replays the *already validated* placements recorded in a variant's
`edits.json` on a native-profile baseline and freezes the result in the exact
on-disk snapshot format `run_scene` expects at
`outputs/no_edit/<run>/scenes/val_<house>/scene`. It is a profile transplant of a
previously accepted layout, not a new obstacle search: no agent, no placement
sampling, no new acceptance claim. The augmented result is re-checked for
finite state and full-height collision so a broken transplant is refused instead
of frozen.

Run from the worktree root, with the MolmoBot client interpreter (needs
matplotlib/Pillow) on PYTHONPATH=src:

    DATAFLOW_PYTHON=/home/wenyifan/wenyifan/MoMaTrajGen/molmospaces/.venv/bin/python \
    MUJOCO_GL=egl MPLCONFIGDIR=outputs/dependencies/mpl \
    PYTHONPATH=src "$DATAFLOW_PYTHON" scripts/rebuild_augmented_native.py \
      --variant outputs/diagnostics/dense-case1-five-20261011-r5-views/variants/v000 \
      --run-id <collect-run-id>
"""
import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(ROOT / 'src'))

from lastmile_dataflow.catalog.edit_assets import EditAssetCatalog  # noqa: E402
from lastmile_dataflow.config import RobotConfig  # noqa: E402
from lastmile_dataflow.io import read_json  # noqa: E402
from lastmile_dataflow.runtime.obstacle_edit_session import ObstacleEditSession  # noqa: E402
from lastmile_dataflow.runtime.preparation import SettleConfig  # noqa: E402
from lastmile_dataflow.runtime.simulation import Simulation  # noqa: E402
from lastmile_dataflow.scenes.source import SceneSource  # noqa: E402
from lastmile_dataflow.stations.no_edit_config import load_no_edit_config  # noqa: E402
from lastmile_dataflow.validation.obstacle_augmentation import collision_pairs  # noqa: E402


def placements_from_edits(path, allow_empty=False):
    """Only the fields `ObstacleEditSession.candidate` consumes; edits.json carries more.

    `allow_empty` is the unedited-twin case: replay nothing, i.e. freeze the plain
    scene under the native profile so the same station set can be scored without
    the added obstacles.
    """
    edits = read_json(path)
    if not isinstance(edits, list) or (not edits and not allow_empty):
        raise ValueError('edits.json must be a nonempty list')
    return [{k: entry[k] for k in ('asset_id', 'pose')} for entry in edits]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--variant', required=True, type=Path)
    parser.add_argument('--run-id', required=True,
                        help='collect-no-edit run id whose scenes/val_<house>/scene is written')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'outputs')
    parser.add_argument('--robot-model', type=Path, default=None,
                        help='defaults to <assets>/robots/rby1m/rby1_v1.2_site_control.xml')
    parser.add_argument('--no-placements', action='store_true',
                        help='freeze the unedited scene under the native profile (empty placement list)')
    parser.add_argument('--collision', action='store_true',
                        help='also re-run full-height collision check (needs the run NoEditConfig)')
    args = parser.parse_args()

    variant = args.variant.resolve()
    edits_path = variant / 'edits.json'
    if not edits_path.is_file() and not args.no_placements:
        raise SystemExit('missing ' + str(edits_path))

    # frozen_config.json of the augmentation run records the exact baseline,
    # asset library, house and settle settings that produced this variant.
    ag_run = variant.parents[1]
    frozen = read_json(ag_run / 'frozen_config.json')
    aug = frozen['augmentation']
    house = int(aug['house'])
    if not aug.get('input_snapshot'):
        raise SystemExit('augmentation frozen_config has no input_snapshot baseline')
    baseline_dir = Path(aug['input_snapshot']).resolve()
    dataset_dir = Path(aug['dataset_dir']).resolve()
    assets_root = dataset_dir.parents[1]                      # .../molmospaces_data/assets
    robot_model = args.robot_model or assets_root / 'robots/rby1m/rby1_v1.2_site_control.xml'
    settle_config = SettleConfig(**aug['settle'])
    catalog_path = ag_run / 'assets/catalog.json'
    if not catalog_path.is_file():
        raise SystemExit('missing ' + str(catalog_path))
    assets = EditAssetCatalog(catalog_path)

    # The native robot collect-no-edit will force; must match cli.py exactly.
    robot = replace(RobotConfig(str(robot_model)),
                    control_profile='molmobot-native-v1', control_hz=10., physics_dt=.004)

    placements = [] if args.no_placements else placements_from_edits(edits_path)
    asset_ids = [p['asset_id'] for p in placements]

    source = SceneSource.procthor(dataset_dir, house)
    # The augmentation baseline is itself an unedited original snapshot, so it
    # cannot be loaded under the native profile (its frozen robot is position-v1).
    # Rebuild the same original scene from source under the native profile; the
    # XML/metadata are unchanged, so object geometry is identical. Refuse if the
    # recorded baseline carried a restoration (that would be an edited input).
    baseline_version = read_json(baseline_dir / 'version.json')
    if baseline_version.get('restoration') is not None:
        raise SystemExit('baseline snapshot carries a restoration; not an original baseline')
    baseline = Simulation.from_source(source, robot, target=aug['target_instance'])
    if baseline.source.provenance() != read_json(baseline_dir / 'version.json')['source']:
        baseline.close()
        raise SystemExit('baseline snapshot source does not match dataset_dir/house')

    destination = args.output_dir.resolve() / 'no_edit' / args.run_id / 'scenes' / f'val_{house}' / 'scene'
    if destination.exists():
        baseline.close()
        raise SystemExit('refusing to overwrite existing frozen scene: ' + str(destination))

    session = ObstacleEditSession(baseline, assets, settle_config)
    try:
        sim = session.candidate(placements)
        recorded = variant / 'control_settling.json'
        duration = float(read_json(recorded).get('simulated_s')) if recorded.is_file() else None
        session.settle(duration_s=duration)
        if not bool(np.isfinite(sim.state_vector()).all()):
            raise SystemExit('nonfinite state after replay')
        collision = None
        if args.collision:
            no_edit_config = load_no_edit_config(ROOT / 'configs/no_edit/curobo_v080.json')
            collision = collision_pairs(sim, placements, no_edit_config, settled=True)
            if not collision['valid']:
                raise SystemExit('replayed layout collides: ' + json.dumps(collision)[:600])
        destination.parent.mkdir(parents=True, exist_ok=True)
        sim.freeze(destination)
    finally:
        session.close()
        baseline.close()

    provenance = {'tool': 'rebuild_augmented_native', 'variant': str(variant),
                  'source_edits': str(edits_path), 'placements': placements, 'asset_ids': asset_ids,
                  'catalog': str(catalog_path), 'robot': asdict(robot),
                  'note': 'replayed accepted placement under native profile; not a new acceptance run'}
    (destination / 'rebuild_provenance.json').write_text(json.dumps(provenance, indent=1, sort_keys=True))
    print(destination)
    print('house', house, 'obstacles', asset_ids)


if __name__ == '__main__':
    main()
