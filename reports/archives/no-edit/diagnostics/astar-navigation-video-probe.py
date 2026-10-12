"""One real navigation-only smoke. No cuRobo manipulation, no scene edits/reset."""
import os
import uuid
from pathlib import Path
from dataclasses import asdict,replace
from lastmile_dataflow.workflows.no_edit import available_gpus
idle=available_gpus(list(range(8)))
if not idle:raise RuntimeError('no_idle_gpu_for_real_rendering')
gpu=idle[0]['index']
os.environ.update(CUDA_VISIBLE_DEVICES=str(gpu),MUJOCO_GL='egl',MUJOCO_EGL_DEVICE_ID=str(gpu))
print('GPU',gpu,flush=True)
import numpy as np
from PIL import Image,ImageDraw
from lastmile_dataflow.io import read_json,write_json
from lastmile_dataflow.config import RobotConfig,CollectionConfig
from lastmile_dataflow.stations.no_edit_config import load_no_edit_config
from lastmile_dataflow.runtime.simulation import Simulation
from lastmile_dataflow.runtime.no_edit_execution import ContinuousContext,navigate,OperationFailure
from lastmile_dataflow.recording.recorder import AttemptRecorder
from lastmile_dataflow.recording.analysis_video import AnalysisVideo
from lastmile_dataflow.stations.no_edit_sampling import place_frozen_station
from lastmile_dataflow.navigation.astar import scene_grid,search,task_support_obstacles
from lastmile_dataflow.exporting.navigation_map import export_navigation_map

class NavigationVideo(AnalysisVideo):
    def frame(self,*args,**kwargs):
        frame=Image.fromarray(super().frame(*args,**kwargs));draw=ImageDraw.Draw(frame)
        draw.rectangle((0,0,960,40),fill='#101820')
        draw.text((18,8),'A* 导航测试 · S0 → S1 · 不执行抓取',font=self.title_font,fill='white')
        return np.asarray(frame)

source=Path('outputs/no_edit/no-edit-val103-cup30-v2-20261010/scenes/val_103').resolve()
tp=source/'tasks/pick-bf2b057e9916'
root=Path('outputs/diagnostics')/('astar-navigation-video-val103-20261010-'+uuid.uuid4().hex[:8])
root.mkdir(parents=True,exist_ok=False)
cfg=replace(load_no_edit_config('configs/no_edit/curobo_v080.json'),attempt_timeout_s=300.)
robot=RobotConfig(**read_json(source/'scene/version.json')['robot'])
collection=CollectionConfig(output_dir=str(root.resolve()),width=cfg.width,height=cfg.height,record_video=True)
task=read_json(tp/'task.json');stations=read_json(tp/'stations.json');stats=read_json(tp/'station_statistics.json')
sim=Simulation.from_snapshot(source/'scene',robot,target=task['target_body'])
recorder=None;ctx=None;navigation={'status':'not_executed'};status='infrastructure_error';reason='unfinished'
write_json(root/'test_plan.json',{'scope':'one real A* navigation-only video, no manipulation',
 'expected':'fresh Simulation, no post-begin reset, reach S1 with no robot collision; support visible before navigation',
 'failure_policy':'stop on first physical/control/infra failure, retain video and evidence',
 'source_scene':str(source/'scene'),'gpu':gpu,'config':asdict(cfg)})
try:
    task={**task,'support_obstacles':task_support_obstacles(sim,task)}
    foot=read_json(source/'robot_geometry.json')
    grid=scene_grid(sim,task['anchor_world'],cfg.radius_m,cfg.map_resolution_m,foot['radius_m'],cfg.navigation_margin_m,
                    support_obstacles=task['support_obstacles'])
    display_grid=scene_grid(sim,task['anchor_world'],cfg.radius_m,cfg.map_resolution_m,0.,0.,
                            support_obstacles=task['support_obstacles'])
    export_navigation_map(root,grid,task['anchor_world'],display_grid=display_grid)
    valid=[s for s in stations if s['geometry']=='valid' and grid.cell(s['xy']) is not None and grid.free[grid.cell(s['xy'])]]
    measured_success={s['station_id'] for s in stats if s['successes']>0}
    goals=[s for s in valid if s['station_id'] in measured_success] or valid
    options=[]
    for goal in goals:
        for start in valid:
            if start['station_id']==goal['station_id']:continue
            path=search(grid,start['xy'],goal['xy'])
            if path and .8<=path['length_m']<=1.6:
                options.append((abs(path['length_m']-1.2),start['station_id'],start,goal,path))
    if not options:raise RuntimeError('no_suitable_distinct_start_goal_in_current_support_aware_grid')
    _,_,start,goal,path=min(options,key=lambda item:item[:2])
    write_json(root/'navigation_request.json',{'task':task,'start':start,'goal':goal,'path':path,
                                             'goal_has_existing_operation_success':goal['station_id'] in measured_success})
    print('OUTPUT',root, 'S0',start['station_id'],'S1',goal['station_id'],'length',path['length_m'],flush=True)
    place_frozen_station(sim,start);sim.enable_third_person()
    packet={'robot':asdict(robot),'collection':asdict(collection),'protocol':asdict(cfg),
            'task':task,'station':start,'goal':goal,'path':path,'source_scene':str(source/'scene')}
    recorder=AttemptRecorder(root,packet,attempt_id='navigation-only',strategy='real_astar_navigation_only')
    ctx=ContinuousContext(sim,recorder,cfg,record_rgb=True,record_png=False)
    ctx.analysis=NavigationVideo(task,start,path=path,goal=goal,radius_m=cfg.radius_m,footprint_radius_m=foot['radius_m'])
    write_json(recorder.path/'initial_state.json',ctx.state())
    navigation=navigate(ctx,path,goal)
    status='success';reason='navigation_only_arrived'
except OperationFailure as exc:
    status='failure';reason=exc.reason;navigation={'status':'failure','reason':reason}
except TimeoutError as exc:
    status='incomplete';reason=str(exc)
except Exception as exc:
    reason=type(exc).__name__+':'+str(exc)
finally:
    if ctx is not None:ctx.finish()
    if recorder is not None:
        recorder.finish(status,reason,{'scene_validity':{'status':'valid','scope':'frozen scene, robot initialization only'},
         'case_condition':{'status':'not_applicable','reason':'navigation-only smoke'},
         'task_completion':{'status':'not_applicable','reason':'no manipulation requested'}},
         extra={'navigation':navigation,'continuous_simulation':True,'no_scene_edits':True,'gpu':gpu})
    outcome={'status':status,'reason':reason,'navigation':navigation,'scope':'real navigation only; not grasp success or dataset qualification',
             'gpu':gpu,'collisions':len(ctx.collisions) if ctx else None,'executed_steps':recorder.steps if recorder else 0,
             'attempt':str(recorder.path) if recorder else None}
    write_json(root/'summary.json',outcome);sim.close()
    print(outcome,flush=True)
