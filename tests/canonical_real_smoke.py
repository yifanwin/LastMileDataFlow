"""Explicit bounded raw-first capture on a verified frozen scene, no planner/VLA.

May use Mesa software EGL to verify rendering; this NEVER proves CUDA planning,
model inference or physical task success. Requires a new output directory.
"""
import argparse
from pathlib import Path

from lastmile_dataflow.config import CollectionConfig, RobotConfig, TaskConfig, construct
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.runtime.runner import run_attempt
from lastmile_dataflow.validation.canonical import audit_canonical


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--steps', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.steps <= 10:
        parser.error('smoke steps must be 1..10')
    args.output.mkdir(parents=True, exist_ok=False)
    version = read_json(args.snapshot / 'version.json')
    robot = construct(RobotConfig, version['robot'])
    path = run_attempt(None, robot, TaskConfig(task_id='raw-first-protocol-smoke'),
                       CollectionConfig(output_dir=str(args.output), max_steps=args.steps, record_video=False),
                       frozen_dir=args.snapshot, attempt_id='capture', strategy='protocol_smoke_not_task_demonstration')
    audit = audit_canonical(path)
    legacy = read_json(path / 'result.json')
    result = {'purpose': 'raw_first_capture_and_rendering_only', 'source_scene_id': version['source']['scene_id'],
              'source_snapshot': str(args.snapshot), 'attempt': str(path), 'audit': audit,
              'termination_status': legacy['status'], 'legacy_executed_steps': legacy['executed_steps'],
              'CUDA_planner_tested': False, 'VLA_inference_tested': False, 'task_success_tested': False}
    write_json(args.output / 'smoke.json', result)
    print(result)
    return 0 if audit['valid'] and legacy['executed_steps'] == args.steps else 1


if __name__ == '__main__':
    raise SystemExit(main())
