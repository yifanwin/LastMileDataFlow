"""Same real reviewer input/images, explicit model comparison; never accepts scenes."""
import argparse
from pathlib import Path
import time
import uuid

from lastmile_dataflow.agents.case_gateway import AgentBudget, CaseGateway, HTTPCaseBackend
from lastmile_dataflow.agents.edit_reviewer import parse_layout_review
from lastmile_dataflow.agents.prompts import REVIEWER
from lastmile_dataflow.construction.scene_request import MobileCaseTemplate
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.scenes.graph import SceneGraph
from lastmile_dataflow.scenes.local_context import LocalContext


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--call', required=True, type=Path, help='new-contract real reviewer call JSON')
    p.add_argument('--api-settings', required=True, type=Path)
    p.add_argument('--provider', required=True, help='explicit provider for a controlled comparison')
    p.add_argument('--models', nargs='+', default=['gpt-5.6-sol', 'glm-5.3'])
    p.add_argument('--timeout', type=float, default=240)
    p.add_argument('--max-calls', type=int, default=4)
    p.add_argument('--structured-output', choices=('auto', 'json_schema', 'json_object', 'text'), default='auto')
    p.add_argument('--output-dir', type=Path, default=Path('outputs/case_construction'))
    args = p.parse_args()
    if args.provider == 'auto':
        p.error('use one explicit provider so endpoint and model changes are not confounded')
    call = read_json(args.call)
    payload = {k: v for k, v in call['request'].items() if k not in ('output_contract', 'previous_response', 'format_feedback')}
    if call['role'] != 'reviewer' or payload.get('review_scope') != 'visible_construction_only':
        p.error('requires a current-contract reviewer input')
    images = call['images']
    if not images or not all(Path(i['path']).is_file() for i in images):
        p.error('actual source RGB must exist; never silently drop images')
    template = MobileCaseTemplate.from_dict(payload['template'])
    ctx = payload['after_context']
    context = LocalContext(ctx['context_id'], ctx['target'], ctx['support'], ctx['radius_m'], SceneGraph.from_dict(ctx['graph']))
    path = args.output_dir/('model-comparison-' + uuid.uuid4().hex[:12])
    path.mkdir(parents=True, exist_ok=False)
    write_json(path/'expectation.json', {'source_call': str(args.call.resolve()), 'provider': args.provider,
        'models': args.models, 'question': '同一真实场景/图片下格式、证据引用及职责遵守是否改善？',
        'not_task_verification': True, 'image_count': len(images), 'timeout_per_call_s': args.timeout,
        'max_calls_per_model': args.max_calls, 'structured_output': args.structured_output})
    records = []
    for index, model in enumerate(args.models):
        start = time.monotonic()
        gateway = CaseGateway(HTTPCaseBackend(args.api_settings, provider=args.provider, model=model,
                              structured_output=args.structured_output),
            AgentBudget(args.max_calls, start+args.timeout*args.max_calls), timeout_s=args.timeout,
            path=path/f'model_{index:02d}'/'agents')
        record = {'model': model, 'provider': args.provider, 'construction_decision_applied': False}
        try:
            result = gateway.call('reviewer', REVIEWER, payload,
                lambda value: parse_layout_review(value, sample_id=payload['sample_id'],
                    expected=payload['required_visual_checks'], views={i['view'] for i in images},
                    template=template, context_after=context), images=images)
            record.update(status='parsed', response=result.to_dict())
        except Exception as exc:
            record.update(status='error', error_type=type(exc).__name__, reason=str(exc)[:2000])
        record.update(calls=gateway.budget.calls, wall_time_s=time.monotonic()-start)
        records.append(record)
        write_json(path/'result.json', {'models': records, 'case_condition': 'unknown', 'task_completion': 'unknown'})
    print(path, flush=True)
    return 0 if all(r['status'] == 'parsed' for r in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
