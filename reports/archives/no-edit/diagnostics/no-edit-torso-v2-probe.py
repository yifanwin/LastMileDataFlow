"""Independent real native check; never rewrites the old failed attempt."""
import os,json,uuid,time
from pathlib import Path
from lastmile_dataflow.workflows.no_edit import available_gpus
cards=available_gpus(list(range(8)))
if not cards:raise RuntimeError('no permitted idle GPU')
gpu=cards[0]['index'];os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu),MUJOCO_GL='egl',MUJOCO_EGL_DEVICE_ID=str(gpu))
print('GPU',gpu,flush=True)
from lastmile_dataflow.io import read_json,write_json
from lastmile_dataflow.config import RobotConfig,CollectionConfig
from lastmile_dataflow.stations.no_edit_config import NoEditConfig
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.no_edit_execution import run_raw_attempt
from lastmile_dataflow.tasks.raw_scene import grasp_candidates
from lastmile_dataflow.recording.timing import GenerationTimer
old=Path('outputs/no_edit/no-edit-val-four-view-full-20261010/scenes/val_0/attempts/pick-99178612c276-S0008-t1-ad427bed').resolve()
p=read_json(old/'config.json');assets=read_json(old.parents[3]/'frozen_config.json')['assets_dir']
root=Path('outputs/no_edit/no-edit-torso-v2-native-probe-20261010').resolve();root.mkdir(exist_ok=False)
timer=GenerationTimer(root/'timing.json');scene=root/'scenes/val_0';scene.mkdir(parents=True)
sim=Simulation.from_snapshot(old.parent.parent/'scene',RobotConfig(**p['robot']))
try:
 sim.freeze(scene/'scene');cfg=NoEditConfig(**p['protocol']);collection=CollectionConfig(**p['collection'])
 write_json(root/'probe_config.json',{'scope':'independent native rerun of one old torso-stopped station; not final dataset','old_attempt':str(old),'protocol':'mobile-pick-v2','torso_error_policy':cfg.torso_error_policy,'seed':p['seed'],'gpu':gpu})
 result=run_raw_attempt(sim,p['task'],p['station'],grasp_candidates(p['task'],assets),assets,cfg,collection,scene,'torso-v2-S0008-'+uuid.uuid4().hex[:8],seed=p['seed'])
 write_json(root/'probe_result.json',result);timer.update(result['status'],final=True);print(json.dumps(result,ensure_ascii=False),flush=True)
finally:sim.close()
