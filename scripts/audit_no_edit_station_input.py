"""Read-only audit of formal augmented-point ingestion, not task success."""
import argparse
from pathlib import Path
from datetime import datetime, timezone
from lastmile_dataflow.io import read_json, file_digest, write_json
from lastmile_dataflow.stations.obstacle_selection import coverage_select


def audit(root):
    root = Path(root); frozen = read_json(root/'frozen_config.json')
    m = frozen['augmented_input']; scene = root/'scenes'/m['scene_id'].split('/')[-1]
    inp = scene/'augmented_input'; task = read_json(inp/'baseline/task.json')
    checks = {'copied_manifest_matches_frozen':read_json(inp/'manifest.json')==m}
    for name, expected in m['files_sha256'].items():
        p = scene/name if name.startswith('scene/') else inp/name
        checks['hash:'+name] = file_digest(p)==expected
    selected = read_json(inp/'stations_final.json')
    original = read_json(inp/'navigation/comparison.json')['stations_after']
    expected,selection = coverage_select(original,task['anchor_world'],20)
    checks['coverage_not_prefix'] = selected==expected and read_json(inp/'station_selection.json')==selection
    checks['at_most_twenty'] = 0<len(selected)<=20
    taskdirs = list((scene/'tasks').glob('*/station_input.json'))
    checks['exactly_one_task'] = len(taskdirs)==1
    trials=[]; attempts=[];progress={}
    ids = set(m['station_ids'])
    if len(taskdirs)==1:
        folder=taskdirs[0].parent
        checks['execution_stations_equal_source'] = read_json(folder/'stations.json')==selected
        info=read_json(folder/'station_input.json')
        checks['no_regeneration'] = info['lattice_regenerated'] is False
        checks['execution_count'] = info['execution_point_count']==len(selected)
        trials=read_json(folder/'trials.json') if (folder/'trials.json').exists() else []
        progress=read_json(folder/'progress.json')
        checks['planned_trial_count'] = progress['expected_trials']==len(selected)*frozen['config']['trials_per_station']
        checks['trials_only_selected'] = all(r['station_id'] in ids for r in trials)
    for p in (scene/'attempts').glob('*/*/*/attempt.json'):
        d=read_json(p)
        # Both station trials and rollouts live under the selected start station.
        checks['attempt:'+d['attempt_id']] = p.parent.parent.name in ids
        attempts.append({'station_id':p.parent.parent.name,'status':d['status'],
                         'executed_steps':d.get('executed_steps'), 'attempt':str(p.parent)})
    return {'scope':'same_context_read_only_station_input_audit_not_task_success',
            'checked_at_utc':datetime.now(timezone.utc).isoformat(),'run_id':root.name,
            'valid':all(checks.values()),'checks':checks,'case':m['case'],
            'final_station_count':len(selected),'original_actual_valid_count':selection['input_count'],
            'method':selection['method'],'expected_station_trials':len(selected)*frozen['config']['trials_per_station'],
            'observed_trials':len(trials),'trial_statuses':{s:sum(r['status']==s for r in trials) for s in {r['status'] for r in trials}},
            'attempts':attempts,'progress':progress,
            'model_service_status':read_json(scene/'model_service_status.json') if (scene/'model_service_status.json').exists() else None,
            'collector_status':read_json(root/'summary.json')['status']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run');p.add_argument('--output',required=True);args=p.parse_args()
    result=audit(args.run);write_json(args.output,result)
    print({k:result[k] for k in ('run_id','valid','final_station_count','expected_station_trials','observed_trials','collector_status')})
    raise SystemExit(0 if result['valid'] else 1)
