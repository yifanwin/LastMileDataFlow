"""显式真实资产验证；不会被 unittest discover 自动运行，不调用旧工程。

只读 ProcTHOR + 真 RBY1 MJCF，最多每个房屋两次 6 步短动作，及一个可选旧样例初态回归。
"""
import argparse
from dataclasses import replace
from pathlib import Path
import sys
import uuid

import imageio.v2 as imageio
import numpy as np

from lastmile_dataflow.config import CollectionConfig, RobotConfig, TaskConfig, load_config
from lastmile_dataflow.io import read_json, write_json
from lastmile_dataflow.runtime.runner import audit_attempt, run_attempt
from lastmile_dataflow.scenes.source import SceneSource

ROOT=Path(__file__).resolve().parents[1]


def check_attempt(path):
    result=read_json(path/'result.json')
    audit=audit_attempt(path)
    summary={'attempt':str(path),'result':result,'audit':audit,'videos':{}}
    videos=read_json(path/'videos.json')
    for camera,info in videos['cameras'].items():
        reader=imageio.get_reader(path/info['path'])
        try:
            count=reader.count_frames()
            pixels=reader.get_data(0)
        finally:reader.close()
        summary['videos'][camera]={'decoded_frames':count,'shape':list(pixels.shape)}
        assert count==info['frame_count'],(camera,count,info['frame_count'])
    assert audit['valid'],audit
    assert result['status']=='execution_complete',result
    assert result['results']['task_completion']['status']=='unknown'
    assert len(summary['videos'])==3,summary['videos']
    return summary


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--houses',nargs='+',type=int,default=[0,2])
    p.add_argument('--dataset-dir',type=Path,default=ROOT.parent/'molmospaces_data/assets/scenes/procthor-10k-train')
    p.add_argument('--legacy-import',type=Path)
    p.add_argument('--prefix',default='phase1-'+uuid.uuid4().hex[:8])
    p.add_argument('--report',type=Path,default=ROOT/'reports/checks/real-smoke.json')
    a=p.parse_args()
    robot=load_config(RobotConfig,ROOT/'configs/robots/rby1.json')
    collection=load_config(CollectionConfig,ROOT/'configs/collection/smoke.json')
    report={'scope':'real_short_action_initialization_snapshot_video_not_grasp_success','attempts':[]}
    try:
        for house in a.houses:
            source=SceneSource.procthor(a.dataset_dir,house)
            objects=read_json(source.metadata_path)['objects']
            targets=[key for key,obj in sorted(objects.items()) if not obj.get('is_static',True)]
            if not targets:raise ValueError(f'house {house} has no dynamic target')
            target=targets[0]
            task=TaskConfig(task_id=f'foundation-{house}',target=target)
            print('raw source',source.scene_id,'target',target,flush=True)
            path=run_attempt(source,robot,task,collection,attempt_id=f'{a.prefix}-train{house}')
            summary=check_attempt(path);report['attempts'].append(summary)
            restored=run_attempt(source,robot,task,collection,frozen_dir=path/'scene',attempt_id=f'{a.prefix}-train{house}-restore')
            summary=check_attempt(restored)
            with np.load(path/'scene/initial.npz',allow_pickle=False) as initial, np.load(restored/'scene/initial.npz',allow_pickle=False) as second:
                np.testing.assert_array_equal(initial['state'],second['state'])
            assert read_json(path/'initial_state.json')==read_json(restored/'initial_state.json')
            summary['initial_state_exact_restore']=True;report['attempts'].append(summary)
            print('passed',path.name,restored.name,flush=True)
        if a.legacy_import:
            imported=read_json(a.legacy_import)
            source=SceneSource(**imported['source'])
            task=TaskConfig(task_id='legacy-initial-state-regression',target=imported['target'])
            print('legacy initial state regression',source.scene_id,flush=True)
            path=run_attempt(source,robot,task,collection,restoration=imported['restoration'],attempt_id=f'{a.prefix}-legacy')
            summary=check_attempt(path)
            target=read_json(path/'initial_state.json')['target']
            pose=imported['restoration']['object_poses'][imported['target']]
            np.testing.assert_allclose(target['pose_world_xyz_wxyz'][:3],pose[:3],atol=1e-7)
            summary['restored_target_position_matches']=True;report['attempts'].append(summary)
        report['status']='passed'
    except Exception as exc:
        report['status']='failed';report['error']=str(exc)
        raise
    finally:
        write_json(a.report,report)
    print(a.report,flush=True)


if __name__=='__main__':main()
