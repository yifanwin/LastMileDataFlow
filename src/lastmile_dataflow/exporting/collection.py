"""Combine audited phase-three runs, keeping controller/config distinctions explicit."""
import re
import shutil
from pathlib import Path
from ..io import read_json,write_json,file_digest
from ..validation.stations import audit_station_attempt
from .station_report import export_report


def verify_manifest(path):
    path=Path(path); manifest=read_json(path/'artifacts.json')['files_sha256']
    actual={str(p.relative_to(path)) for p in path.rglob('*') if p.is_file() and p.name!='artifacts.json'}
    if actual!=set(manifest): raise ValueError('run artifact set mismatch')
    for name,sha in manifest.items():
        f=path/name
        if not f.resolve().is_relative_to(path.resolve()) or not f.is_file() or file_digest(f)!=sha: raise ValueError('run artifact modified')


def export_collection(runs,output_dir,*,collection_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',collection_id): raise ValueError('unsafe collection id')
    # Validate all evidence BEFORE creating the collection directory.
    packets=[]; rows=[]; audits={}; scene=None; target=None; attempts=set(); conditions=[]
    for directory in runs:
        run=Path(directory).resolve(); verify_manifest(run)
        inputs=read_json(run/'frozen_inputs.json'); summary=read_json(run/'summary.json')
        if inputs['schema_version']!='3.0': raise ValueError('unsupported collection source')
        identity=(inputs['scene_version'],inputs['stations']['target'])
        if scene is None: scene,target=identity
        if identity!=(scene,target): raise ValueError('collection must share frozen scene and target')
        packets.append({'run':str(run),'input_digest':inputs['input_digest'],'artifact_manifest_sha256':file_digest(run/'artifacts.json'),
                        'approach_offset_m':inputs['stations'].get('approach_offset_m',0.),'summary':summary})
        conditions.append({'run_id':summary['run_id'],**summary['case_condition']})
        for source in read_json(run/'stations.json'):
            row={**source,'run_id':summary['run_id'],'approach_offset_m':inputs['stations'].get('approach_offset_m',0.)}
            attempt=row.get('attempt')
            if attempt:
                if attempt in attempts: raise ValueError('duplicate attempt in collection')
                attempts.add(attempt); audit=audit_station_attempt(attempt); audits[attempt]=audit
                if not audit['valid']: raise ValueError('attempt evidence failed independent audit')
                row['attempt_manifest_sha256']=file_digest(Path(attempt)/'artifacts.json')
            rows.append(row)
    if not packets: raise ValueError('empty collection')
    path=Path(output_dir)/'collections'/collection_id;path.mkdir(parents=True,exist_ok=False)
    # The largest run is the coverage map; retain each independent map in the report.
    representative=max(packets,key=lambda p:len(read_json(Path(p['run'])/'candidates.json')))
    shutil.copyfile(Path(representative['run'])/'map_geometry.json',path/'map_geometry.json')
    write_json(path/'frozen_inputs.json',{'schema_version':'3.0','scene_version':scene,'target':target,'runs':packets})
    write_json(path/'stations.json',rows);write_json(path/'audit.json',{'valid':True,'attempts':audits})
    passed=[c for c in conditions if c['status']=='pass']
    case={'status':'pass' if passed else 'unknown','reason':'bounded_station_witnesses' if passed else 'no_verified_case_run',
          'scope':'per-run frozen controller/arm/torso/yaw/grasp budgets; not global reachability or navigation',
          'by_run':conditions}
    summary={'schema_version':'3.0','scene_version':scene,'successes':sum(r['execution']=='success' for r in rows),
             'failures':sum(r['execution']=='failure' for r in rows),'infrastructure_errors':sum(r['status']=='infrastructure_error' for r in rows),
             'planning_no_solution':sum(r['status']=='planning_no_solution' for r in rows),'geometry_filtered':sum(r['geometry']=='geometry_filtered' for r in rows),
             'not_tested':sum(r['status']=='not_tested' for r in rows),'case_condition':case,'data_collection_complete':False,
             'coverage_run':representative['run'],'controller_comparison':'0 mm vs 10 mm approach offset is a control variable, not a station-only effect'}
    summary['data_collection_complete']=bool(summary['successes'] and summary['failures'] and passed)
    write_json(path/'summary.json',summary);export_report(path)
    report=(path/'REPORT.md').read_text()
    report=report.replace('## 本次真实执行视频', '## 抓法配置必须分开解释\n\n0 mm 与 10 mm 接近深度是两个冻结控制配置；同一近站位出现成败不能归因于站位本身。远起点困难与近点可解只在相应配置与有限规划预算内成立。没有连续导航证据。\n\n## 本次真实执行视频')
    report+='\n## 各 run 的覆盖和原始报告\n\n'
    import os
    for packet in packets:
        run=Path(packet['run']);rel=os.path.relpath(run,path)
        report+=f"- [{run.name}]({rel}/REPORT.html)：接近深度 {packet['approach_offset_m']*1000:g} mm。\n"
    (path/'REPORT.md').write_text(report,encoding='utf-8')
    # Package contains small indexes and figures. Large raw videos/trajectories stay in immutable attempts.
    write_json(path/'artifacts.json',{'files_sha256':{str(p.relative_to(path)):file_digest(p) for p in path.rglob('*') if p.is_file() and p.name!='artifacts.json'}})
    return path
