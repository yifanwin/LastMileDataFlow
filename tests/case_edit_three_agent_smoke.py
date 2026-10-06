"""REAL three-model-role calls and six RGB on a SYNTHETIC scene, not ProcTHOR proof."""
import argparse
from dataclasses import asdict
from pathlib import Path
import time
from case_edit_helpers import table_scene
from lastmile_dataflow.config import CollectionConfig
from lastmile_dataflow.construction.case_schema import CaseEditRequest, GenerationConfig
from lastmile_dataflow.io import read_json
from lastmile_dataflow.recording.edit_views import ViewConfig
from lastmile_dataflow.workflows.case_edit import run_case_edit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--api-settings', required=True)
    args = parser.parse_args()
    inputs = Path('outputs/case_edits/three-agent-inputs-' + str(time.time_ns()))
    inputs.mkdir(parents=True, exist_ok=False)
    source, robot = table_scene(inputs)
    request = CaseEditRequest('保持一个目标物体在同一支撑面上，将其布置到明显不同的位置，使前后布局变化清晰可见。',
        asdict(source), task_context={'robot_base': [0, 0, 0]},
        generation=GenerationConfig(target_scene_count=1, max_rounds=2, max_samples=8,
                                    samples_per_proposal=4, max_agent_calls=12, timeout_s=240))
    path = run_case_edit(request, robot, CollectionConfig(output_dir='outputs'), api_settings=args.api_settings,
                        view_config=ViewConfig(width=640, height=480))
    result = read_json(path/'result.json')
    print(result['status'], result['accepted_count'], path, flush=True)
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
