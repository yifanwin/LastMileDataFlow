"""Native case-factory preparation, independent solver probes and strict attempts.

No solver state becomes an execution trajectory. Every execution uses a fresh Simulation.
"""
import copy
from dataclasses import asdict
from pathlib import Path
import itertools
import time
import numpy as np
import mujoco

from ..io import read_json,write_json,digest,file_digest
from ..runtime.preparation import prepare_scene,SettleConfig,scoped_stability
from ..runtime.simulation import Simulation
from ..scenes.geometry import SupportRegion
from ..catalog.factory_index import graph_candidates
from ..grasping.cache import load_grasp_cache
from ..grasping.geometry import object_meshes,asset_collision_digest
from ..stations.config import StationConfig
from ..stations.sampling import edge_samples,place_base,filter_station
from ..stations.execution import Budget,run_station_attempt
from ..planning.curobo import NativePlanner,body_pose
from ..planning.reach import NativeReach
from ..recording.head_views import head_frame
from ..recording.edit_views import ViewConfig
from ..navigation.grid import scene_grid,ground_path
from ..robots.action import torso_joints


# Source scenes are observed, not gated as a whole: full-scene penetration and solver warnings stay hard
# checks, while stability is required only of each candidate's task-relevant bodies, judged by displacement
# over a 1 s window (slow sliding >6 mm/s is still caught; contact chatter is a warning).
FACTORY_SETTLE={'settle_s':1.,'max_settle_s':3.,'window_s':1.,'require_source_stability':False,'gate_on_velocity':False}
SCOPE_RADIUS_M=.5


def task_scope(graph,candidate,radius_m=SCOPE_RADIUS_M):
    """Target plus objects on the same support within radius; the support too if it can move."""
    nodes=graph.nodes; target=np.asarray(nodes[candidate['target']]['pose']['position'])
    scope={candidate['target']}
    if nodes.get(candidate['support'],{}).get('root_motion')=='free': scope.add(candidate['support'])
    for edge in graph.edges:
        if edge['predicate']!='supported_by' or edge['args'][1]!=candidate['support']: continue
        pose=nodes.get(edge['args'][0],{}).get('pose')
        if pose and np.linalg.norm(np.asarray(pose['position'])[:2]-target[:2])<=radius_m: scope.add(edge['args'][0])
    return scope


class FactoryDependencyError(RuntimeError):
    """Required measured inputs are not ready; not a physical/planning failure."""


class NativeFactoryBackend:
    def __init__(self,robot,collection,config,*,reviewer=None,review_config=None):
        self.robot,self.collection,self.config=robot,collection,config
        self.capability=read_json(config['capability_path'])
        if self.capability.get('measurement_status')!='measured' or self.capability.get('model_sha256')!=file_digest(robot.model_path):
            raise FactoryDependencyError('measured capability for current RBY model required')
        compatible=[]
        root=Path(config['grasp_root'])
        for manifest in sorted(root.glob('*/cache/manifest.json'))+sorted(root.glob('*/manifest.json')):
            try:
                packet,rows=load_grasp_cache(manifest.parent,gripper_digest=self.capability['gripper']['digest'])
                compatible.append(packet['asset_id'])
            except (ValueError,OSError,KeyError,TypeError): continue
        if not compatible:
            raise FactoryDependencyError('no compatible simulation-verified RBY-1 grasp cache; run grasp-generate first')
        self.reviewer,self.review_config=reviewer,review_config
        self.prepared=None; self.serial=0; self.current_path=None
        self.settle_config=SettleConfig(**{**FACTORY_SETTLE,**config.get('settling',{})})

    def frozen_inputs(self):
        import os
        return {'robot':asdict(self.robot),'collection':asdict(self.collection),'factory':self.config,
                'capability':self.capability,'settling':asdict(self.settle_config),'scope_radius_m':SCOPE_RADIUS_M,
                'devices':{k:os.environ.get(k) for k in ('CUDA_VISIBLE_DEVICES','MUJOCO_EGL_DEVICE_ID','MUJOCO_GL')}}

    def prepare(self,source,spec,path):
        self.close(); self.current_path=Path(path)
        self.prepared=prepare_scene(source,self.robot,self.collection,settle_config=self.settle_config,path=self.current_path)
        candidates,rejected=[],[]
        for row in graph_candidates(self.prepared.graph,spec):
            row['snapshot']=str((self.current_path/'scene').resolve())
            row['scope_stability']=scoped_stability(self.prepared.settling,self.settle_config,task_scope(self.prepared.graph,row))
            (candidates if row['scope_stability']['stable'] else rejected).append(row)
        write_json(self.current_path/'candidates.json',candidates)
        write_json(self.current_path/'candidates_rejected.json',[{**r,'reason':'task_scope_unstable'} for r in rejected])
        return candidates

    def _station_config(self,candidate):
        path=Path(self.config['grasp_root'])/candidate['asset_id']
        if (path/'cache/manifest.json').is_file(): path=path/'cache'
        geometry_digest=asset_collision_digest(object_meshes(self.prepared.sim,self.prepared.sim.model.body(candidate['target']).id))
        packet,rows=load_grasp_cache(path,gripper_digest=self.capability['gripper']['digest'],
            asset_id=candidate['asset_id'],asset_digest=geometry_digest)
        ids=[r['grasp_id'] for r in rows][:self.config.get('max_grasps',4)]
        return StationConfig(target=candidate['target'],asset_id=candidate['asset_id'],
            grasp_path=str((path/'grasps.npz').resolve()),grasp_sha256=packet['npz_sha256'],
            robot_planner_dir=self.config['robot_planner_dir'],grasp_ids=ids,
            arms=['left','right'],torso_heights=self.config.get('torso_heights',[0.,.369,.738]),
            num_ik_seeds=self.config.get('num_ik_seeds',32),max_attempts=self.config.get('max_attempts',3),
            seed=self.config.get('seed',0),timeout_s=self.config.get('station_timeout_s',180.),
            max_plans=1000,max_executions=20,render_video=self.config.get('render_video',True))

    def station_map(self,candidate,spec,path):
        self.station_config=self._station_config(candidate); config=self.station_config
        region=SupportRegion(**candidate['region']); sim=self.prepared.sim
        target=sim.data.xpos[sim.model.body(candidate['target']).id].copy()
        samples=edge_samples(region,target,base_radius_m=self.capability['base_footprint_m']['rotation_radius_m'],
            edge_gap_m=self.config.get('edge_gap_m',.1),spacing_m=self.config.get('spacing_m',.3))
        if len(samples)>self.config.get('max_stations',80):
            raise ValueError('edge sampling exceeds station budget; incomplete edge is not evidence')
        self.map_data=scene_grid(sim,radius_m=self.capability['base_footprint_m']['rotation_radius_m'])
        path=Path(path); path.mkdir(parents=True,exist_ok=False)
        rows=[]
        for sample in samples:
            row=self.evaluate(candidate,sample,path/sample['station_id'])
            rows.append(row); write_json(path/'stations.json',rows)
        self.map_rows=rows
        return rows

    def evaluate(self,candidate,sample,path):
        path=Path(path); path.mkdir(parents=True,exist_ok=False); config=self.station_config
        sim=Simulation.from_snapshot(candidate['snapshot'],self.robot,target=candidate['target']); start=time.monotonic()
        version=read_json(Path(candidate['snapshot'])/'version.json')['version_id']
        policy={'scene_version':version,'target':candidate['target'],'grasp_digest':config.grasp_sha256,
                'grasp_ids':config.grasp_ids,'arms':['left','right'],'torso_heights':config.torso_heights,
                'seed':config.seed,'num_ik_seeds':config.num_ik_seeds,'max_attempts':config.max_attempts,
                'timeout_s':config.timeout_s,'protocol':'strict-pick-v3'}
        row={**sample,'reach':'unknown','plan':'unknown','execution':'not_executed','legal':False,'visible':False,
             'facing_target':True,'policy':policy,'evidence':[str((path/'probe.json').resolve())]}
        trials=[]
        try:
            place_base(sim,sample['base']); row['geometry_check']=filter_station(sim,self.collection)
            row['legal']=row['geometry_check']['status']=='valid'
            if not row['legal']: return row
            _,_,visible=head_frame(sim,candidate['target'],ViewConfig(width=320,height=240))
            row['visible']=visible['visible_pixels']>=self.config.get('min_target_pixels',10)
            from ..planning.curobo import load_grasps
            grasps=load_grasps(config,sim); goals={i:body_pose(sim,sim.target_id)@grasps[i] for i in config.grasp_ids}
            reach_success=False; plan_success=False
            initial_q=sim.data.qpos.copy()
            for side,h in itertools.product(config.arms,config.torso_heights):
                if time.monotonic()-start>config.timeout_s: raise TimeoutError('station_budget')
                sim.data.qpos[:]=initial_q
                for n,q in zip(sim.robot.groups['torso'],torso_joints(h)): sim.data.qpos[sim.robot.addresses[n]]=q
                sim.robot.hold(); mujoco.mj_forward(sim.model,sim.data)
                ik=NativeReach(sim,config,side)
                reachable=[]
                for gid in config.grasp_ids:
                    outcome=ik.solve(goals[gid]); trials.append({'arm':side,'torso_h':h,'grasp_id':gid,'reach':outcome})
                    if outcome=='success': reachable.append(gid); reach_success=True
                del ik
                if not reachable: continue
                planning_dir=path/f'{side}-h{h:.4f}'; planning_dir.mkdir()
                planner=NativePlanner(sim,config,side,planning_dir)
                arm_initial=sim.robot.group(side+'_arm').copy()
                for gid in reachable:
                    for n,q in zip(sim.robot.groups[side+'_arm'],arm_initial): sim.data.qpos[sim.robot.addresses[n]]=q
                    mujoco.mj_forward(sim.model,sim.data)
                    goal=goals[gid]; pre=goal.copy(); pre[:3,3]-=goal[:3,2]*.08
                    lift=goal.copy(); lift[2,3]+=.12
                    retreat=lift.copy(); retreat[:3,3]-=goal[:3,2]*.08
                    segment_results=[]; okay=True
                    for phase,pose in (('pregrasp',pre),('approach',goal),('lift',lift),('retreat',retreat)):
                        if time.monotonic()-start>config.timeout_s: raise TimeoutError('station_budget')
                        planned=planner.plan(pose); segment_results.append({'phase':phase,'status':planned.status,'diagnostics':planned.diagnostics})
                        if planned.status not in ('success','no_solution'): raise RuntimeError('planner_infrastructure_failure')
                        if planned.status!='success': okay=False; break
                        # Preparation-only virtual planner state, NEVER saved as executed motion.
                        for n,q in zip(sim.robot.groups[side+'_arm'],planned.positions[-1]): sim.data.qpos[sim.robot.addresses[n]]=q
                        mujoco.mj_forward(sim.model,sim.data)
                    trials.append({'arm':side,'torso_h':h,'grasp_id':gid,'segments':segment_results})
                    if okay:
                        plan_success=True; row['witness']={'arm':side,'torso_h':h,'grasp_id':gid}; break
                del planner
                if plan_success: break
            row.update(reach='success' if reach_success else 'no_solution',plan='success' if plan_success else 'no_solution')
        except TimeoutError:
            row.update(reach='success' if locals().get('reach_success') else 'budget_exhausted',plan='budget_exhausted')
        except Exception as exc:
            row.update(reach='success' if locals().get('reach_success') else 'infrastructure_error',plan='infrastructure_error',error_type=type(exc).__name__)
        finally:
            sim.close(); write_json(path/'probe.json',{'station':row,'trials':trials,'wall_time_s':time.monotonic()-start,'scope':'planning_only_no_actual_motion'})
        return row

    def pair_packet(self,candidate,spec,s0,s1,path):
        yaw=[s0]; path=Path(path); path.mkdir(parents=True,exist_ok=False)
        for i,offset in enumerate(self.config.get('yaw_offsets_rad',[-1.57,-.785,.785,1.57,3.141592653589793])):
            sample={k:copy.deepcopy(s0[k]) for k in ('station_id','edge','base','edge_gap_m','level')}
            sample['station_id']=s0['station_id']+f'-yaw{i}'; sample['base'][2]=float(np.arctan2(np.sin(s0['base'][2]+offset),np.cos(s0['base'][2]+offset)))
            yaw.append(self.evaluate(candidate,sample,path/sample['station_id']))
        edge=[r for r in self.map_rows if r['edge']==s0['edge'] and r['legal']]
        return {'C0':s0,'C_yaw':yaw,'C1':s1,'start_edge':edge,
                'coverage':{'yaw_complete':True,'start_edge_complete':True},
                'path':ground_path(self.map_data,s0['base'],s1['base']),
                'success_region_count':sum(r['plan']=='success' and r['edge']==s1['edge'] for r in self.map_rows),
                'counterfactual':None,'standing':None}

    def execute_pair(self,candidate,packet,path):
        config=self.station_config; witness=packet['C1'].get('witness')
        if not witness: return packet
        from ..validation.stations import audit_station_attempt
        for key in ('C0','C1'):
            row=packet[key]; sample={k:row[k] for k in ('station_id','base','level')}
            self.serial+=1; attempt_id=Path(path).name+f'-{key}-{self.serial}'
            attempt=run_station_attempt(candidate['snapshot'],self.robot,config,self.collection,sample,
                witness['arm'],witness['torso_h'],witness['grasp_id'],path,attempt_id,Budget(config))
            audit=audit_station_attempt(attempt); layers=read_json(attempt/'layers.json'); result=read_json(attempt/'result.json')
            row['evidence'].append(str(attempt.resolve()))
            row['execution']=layers['execution'] if audit['valid'] else 'infrastructure_error'
            row['strict_attempt']={'status':result['status'],'audit':audit,'path':str(attempt.resolve()),'protocol':'strict-pick-v3'}
        return packet

    def edited_candidates(self,candidate,spec,path):
        # Fail explicitly; no unvalidated direct qpos edits or fabricated fallback success.
        write_json(Path(path)/'editing_pending.json',{'status':'unknown','reason':'native_recipe_transactions_not_connected',
                   'recipes':spec.edit_recipes,'original_snapshot':candidate['snapshot']})
        return []

    def review(self,candidate,packet,path):
        if self.reviewer is None: return {'conclusion':'not_requested','calls':0,'calibration_status':'deferred_by_user'}
        from ..recording.factory_views import capture_review_views
        from ..agents.plausibility_reviewer import review_once
        sim=Simulation.from_snapshot(candidate['snapshot'],self.robot,target=candidate['target'])
        try:
            place_base(sim,packet['C0']['base'])
            images=capture_review_views(sim,candidate['target'],SupportRegion(**candidate['region']),path)
            return review_once(self.reviewer,{'target_category':candidate['category']},'unedited',images,['1'],self.review_config)
        except Exception as exc:
            return {'conclusion':'unknown','manual_review':True,'calls':0,'reason':type(exc).__name__}
        finally: sim.close()

    def close(self):
        if self.prepared is not None: self.prepared.close(); self.prepared=None
