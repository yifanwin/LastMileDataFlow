"""Compare telemetry on independent snapshot stepping, without planning/RGB."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import time
from types import ModuleType

import numpy as np

from lastmile_dataflow.config import CollectionConfig, robot_config
from lastmile_dataflow.data.recorder import AttemptRecorder
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.execution.context import ContinuousContext, clone_sim
from lastmile_dataflow.runtime.execution_config import ExecutionConfig
from lastmile_dataflow.runtime.outcomes.pick import PickMonitor


def revision_module(reference, name):
    source = subprocess.check_output(['git', 'show', f'{reference}:src/{name.replace(".", "/")}.py'], text=True)
    module = ModuleType(name + '_probe_baseline')
    module.__package__ = name.rsplit('.', 1)[0]
    exec(compile(source, f'{reference}:{name}', 'exec'), module.__dict__)
    return module


def run(baseline, context_type, monitor_type, root, name, ticks):
    sim = clone_sim(baseline, target=baseline.model.body(baseline.target_id).name)
    recorder = AttemptRecorder(root, {'collection': asdict(CollectionConfig(
        record_images=False, record_video=False, record_physics=False))}, attempt_id=name)
    recorder.bind_simulation(sim)
    context = context_type(sim, recorder, ExecutionConfig(attempt_timeout_s=3000.))
    context.monitor = monitor_type(sim, sim.robot.arms[0])
    context.monitor.initial.update(workspace_center_xy=sim.robot.base_pose()[:2].tolist(), workspace_radius_m=2.)
    context.fingers = context.monitor.allowed_target_fingers
    context.phase = 'telemetry_probe'
    try:
        started = time.perf_counter()
        for _ in range(ticks):
            context.tick(sim.robot.neutral_action())
        elapsed = time.perf_counter() - started
        facts = context.samples
        final_state = sim.state_vector().copy()
        metrics = context.contact_stats.metrics()
    finally:
        context.finish()
        recorder.finish('execution_complete', 'telemetry_probe_without_task_verdict')
        sim.close()
    return elapsed, facts, final_state, metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', required=True, type=Path)
    parser.add_argument('--target', required=True)
    parser.add_argument('--baseline-ref', required=True)
    parser.add_argument('--ticks', type=int, default=10)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads((args.scene / 'config.json').read_text())
    baseline = Simulation.from_snapshot(args.scene, robot_config(config['robot']), target=args.target)
    old_contacts = revision_module(args.baseline_ref, 'lastmile_dataflow.runtime.outcomes.contacts')
    old_pick = revision_module(args.baseline_ref, 'lastmile_dataflow.runtime.outcomes.pick')
    old_context = revision_module(args.baseline_ref, 'lastmile_dataflow.runtime.execution.context')
    old_context.contact_observations = old_contacts.contact_observations
    versions = {'before': (old_context.ContinuousContext, old_pick.PickMonitor),
                'after': (ContinuousContext, PickMonitor)}
    measurements = []
    try:
        for repeat in range(-1, args.repeats):
            results = {}
            for name in (('before', 'after') if repeat % 2 == 0 else ('after', 'before')):
                results[name] = run(baseline, *versions[name], args.output, f'{repeat+1}-{name}', args.ticks)
            before, after = results['before'], results['after']
            np.testing.assert_array_equal(before[2], after[2])
            assert before[1] == after[1]
            assert before[3] == after[3]
            if repeat >= 0:
                measurements.append({'repeat': repeat, 'before_s': before[0], 'after_s': after[0],
                                     'physics_samples': len(after[1])})
    finally:
        baseline.close()
    before = float(np.median([row['before_s'] for row in measurements]))
    after = float(np.median([row['after_s'] for row in measurements]))
    report = {'scope': 'MuJoCo snapshot neutral stepping and physics telemetry; no planner, RGB or task verdict',
              'source_scene': str(args.scene.resolve()), 'target': args.target, 'baseline_ref': args.baseline_ref,
              'ticks_per_attempt': args.ticks, 'warmup_pairs': 1, 'measurements': measurements,
              'all_final_states_facts_and_contact_metrics_equal': True,
              'median_before_s': before, 'median_after_s': after, 'speedup': before/after}
    (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
