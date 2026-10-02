"""Opt-in real ProcTHOR/RBY-1 build/restore/three-camera handoff, not grasp proof."""
import argparse
import uuid
from dataclasses import replace
from pathlib import Path
import numpy as np

from lastmile_dataflow.config import RobotConfig,CollectionConfig,load_config
from lastmile_dataflow.construction.config import BuildConfig,BuildProtocol,BuildBudget
from lastmile_dataflow.io import read_json,write_json,file_digest
from lastmile_dataflow.scenes.source import SceneSource
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.workflows.build import run_build
from real_smoke import check_attempt

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--prefix',default='phase2-'+uuid.uuid4().hex[:8])
    parser.add_argument('--report',type=Path,default=ROOT/'reports/checks/phase2-real-smoke.json')
    args=parser.parse_args()
    robot=load_config(RobotConfig,ROOT/'configs/robots/rby1.json')
    collection=load_config(CollectionConfig,ROOT/'configs/collection/smoke.json')
    collection=replace(collection,max_steps=6)
    report={'scope':'real_scene_build_support_snapshot_three_camera_handoff_not_grasp','builds':[],'initialization':'verified_unedited_phase1_compiled_model_no_fresh_remote_mesh_compile','raw_source_hash_recheck':'not_performed_offline_frozen_input'}
    try:
        for house,edit in [(0,False),(0,True),(2,True)]:
            old=ROOT/f'outputs/attempts/phase1-final-train{house}'
            initial=read_json(old/'initial_state.json'); meta={o['instance_id']:o for o in read_json(old/'scene/instances.json')}
            target=next(n for n,o in meta.items() if o['category']==('SaltShaker' if house==0 else 'AlarmClock'))
            support=meta[target]['parent_instance_id']; provenance=read_json(old/'scene/version.json')['source']
            source=SceneSource(**{k:provenance[k] for k in ('scene_id','xml_path','metadata_path','dataset','split')})
            pose=initial['instances'][target]['pose_world_xyz_wxyz'].copy()
            if edit: pose[1]+=.025 if house==0 else .015
            operations=[{'op':'move','instance':target,'pose':pose}] if edit else []
            config=BuildConfig('2.0',f'real-case1-train{house}','case1',target,support,
                               editable=[target],robot_base=initial['robot']['base'],parameters={'distance_range_m':[.3,8.]},
                               initial_operations=operations,protocol=BuildProtocol(),budget=BuildBudget(timeout_s=300))
            name=f'{args.prefix}-train{house}-'+('move' if edit else 'zero')
            write_json(ROOT/f'configs/builds/{name}.json',{k:v for k,v in config.__dict__.items() if k not in ('protocol','budget')} | {'protocol':config.protocol.__dict__,'budget':config.budget.__dict__})
            before=file_digest(old/'scene/model.mjb')
            print('building',name,flush=True)
            path=run_build(source,robot,config,collection,build_id=name,initial_frozen_dir=old/"scene")
            result=read_json(path/'result.json');summary={'build':str(path),'result':result};report['builds'].append(summary)
            assert before==file_digest(old/'scene/model.mjb'),'frozen foundation edited'
            assert result['status']=='candidate_ready',result
            candidate=read_json(path/'task_candidate.json');summary['candidate']=candidate
            assert candidate['results']['case_condition']['status']=='unknown'
            handoff=read_json(path/'handoff.json');assert handoff['independent_restore_exact']
            attempt=Path(read_json(path/'regression.json')['attempt']);summary['attempt']=check_attempt(attempt)
            if edit:
                transactions=sorted((path/'transactions').glob('*.json'));assert any(read_json(x)['status']=='committed' for x in transactions)
                restored=Simulation.from_snapshot(candidate['scene_dir'],robot,target=target)
                try:
                    actual=restored.data.xpos[restored.model.body(target).id].copy()
                    assert np.linalg.norm(actual[:2]-np.array(initial['instances'][target]['pose_world_xyz_wxyz'][:2]))>.005
                    summary['actual_target_xy_displacement_m']=float(np.linalg.norm(actual[:2]-np.array(initial['instances'][target]['pose_world_xyz_wxyz'][:2])))
                finally:restored.close()
            print('passed',name,flush=True)
        assert report['builds'][0]['candidate']['checkpoint_id']!=report['builds'][1]['candidate']['checkpoint_id']
        report['status']='passed'
    except Exception as exc:
        report.update(status='failed',error=str(exc));raise
    finally:write_json(args.report,report)

if __name__=='__main__':main()
