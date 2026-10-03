"""Optional vision gateway: version-bound finite decisions, no execution privileges.

Every observation is program-generated: geometry, candidates and evidence come from the runner, and
images are diagnostic only (`vla_input: False`). The gateway's decisions are limited to selecting a
program-enumerated candidate, ranking/subsetting those candidates, requesting more views, querying a
region, or abandoning with a reason — it can never supply a number or gain edit privileges.
"""
import copy
import json
from pathlib import Path
import queue
import threading
import time
import mujoco
import numpy as np
import imageio.v2 as imageio
from ..io import digest, write_json

BASE_VIEWS = ('diagnostic_top', 'diagnostic_target', 'diagnostic_side', 'robot_head')
CASE_VIEWS = {'case1': ('diagnostic_distribution',), 'case1.5': ('diagnostic_furniture_sides',)}


class VisionCallBudget:
    """Per-purpose accounting: the whole point is to see *which* judgement consumed the budget."""

    def __init__(self, limits):
        self.limits = dict(limits)
        self.used = {key: 0 for key in self.limits}

    def consume(self, purpose, count=1):
        if purpose not in self.used: raise ValueError(f'unknown agent purpose: {purpose}')
        if self.used[purpose] + count > self.limits[purpose]:
            raise RuntimeError(f'agent_budget_exhausted:{purpose}')
        self.used[purpose] += count

    def remaining(self):
        return {key: self.limits[key] - self.used[key] for key in self.limits}


def case_views(session, views=None):
    """Baseline diagnostic views plus the case-specific diagram, in a stable order."""
    wanted = list(views) if views else list(BASE_VIEWS) + list(CASE_VIEWS.get(session.config.case_type, ()))
    return [v for v in wanted if v not in CASE_VIEWS.get(session.config.case_type, ())] + \
           [v for v in wanted if v in CASE_VIEWS.get(session.config.case_type, ())]


def observe(session, candidates, path, *, stage='settled', images=False, views=None):
    session.active()
    if stage not in ('before_edit','after_edit_unsettled','settled'): raise ValueError('unknown observation stage')
    path=Path(path); path.mkdir(parents=True,exist_ok=False)
    packet={'schema_version':'2.0','revision':session.revision,'model_id':session.model_id,
            'checkpoint_id':session.checkpoint_id,'stage':stage,'state':session.sim.observe_state(),
            'regions':[r.to_dict() for r in session.placements.values()], 'candidates':candidates,
            'requirements':session.last_check,'protected':session.config.protected,'images':[],
            'decision_source':'rule','not_vla_input':True,'case_type':session.config.case_type}
    packet['frames']=frame_evidence(session)
    if images:
        packet['images']=render_views(session,path,candidates,stage,views)
    packet['observation_id']=digest({k:v for k,v in packet.items() if k!='observation_id'})
    write_json(path/'observation.json',packet)
    return packet


def frame_evidence(session):
    """The frame declaration travels with every observation, so orientation cannot drift."""
    from ..construction.cases import constructor
    try:
        case, frames = constructor(session)
    except Exception as exc:
        return {'available': False, 'reason': f'{type(exc).__name__}:{exc}'}
    return {'available': True, 'case_frames': {name: frame.to_dict() for name, frame in frames.items()},
            'convention': 'furniture/region-relative quantities are local; robot-facing headings are world'}


def render_views(session, path, candidates, stage, views=None):
    """320x240 diagnostic renders; the case diagram is drawn from the same candidate set."""
    images=[]
    ordered=case_views(session,views)
    needs_renderer=any(v in BASE_VIEWS for v in ordered)
    renderer=mujoco.Renderer(session.sim.model,height=240,width=320) if needs_renderer else None
    try:
        target=session.sim.data.xpos[session.sim.model.body(session.config.target).id]
        for name in ordered:
            if name in BASE_VIEWS:
                if name=='robot_head':
                    camera=session.sim.robot.camera_names['head_camera']; diagnostic=False
                else:
                    camera=mujoco.MjvCamera(); camera.lookat=target
                    camera.distance=1.8 if name=='diagnostic_top' else .7
                    camera.azimuth=90 if name=='diagnostic_side' else 0
                    camera.elevation=-89 if name=='diagnostic_top' else -15 if name=='diagnostic_side' else -35
                    diagnostic=True
                renderer.update_scene(session.sim.data,camera=camera)
                imageio.imwrite(path/f'{name}.png',renderer.render())
                images.append({'view':name,'path':str(path/f'{name}.png'),'diagnostic':diagnostic,
                               'stage':stage,'vla_input':False,'schematic':False})
                continue
            images.append(diagram(session,path,name,candidates,stage))
    finally:
        if renderer is not None: renderer.close()
    return images


def diagram(session, path, name, candidates, stage):
    """Case-specific plan diagram; recorded as schematic, never as a camera observation."""
    from ..exporting import diagnostics
    from ..construction.cases import constructor
    if name not in CASE_VIEWS.get(session.config.case_type, ()):
        return {'view':name,'path':None,'diagnostic':True,'stage':stage,'vla_input':False,
                'schematic':True,'available':False,'reason':'view_not_defined_for_case'}
    try:
        case, frames = constructor(session)
    except Exception as exc:
        return {'view':name,'path':None,'diagnostic':True,'stage':stage,'vla_input':False,
                'schematic':True,'available':False,'reason':f'{type(exc).__name__}:{exc}'}
    destination=path/f'{name}.png'
    if name=='diagnostic_distribution':
        info=diagnostics.candidate_distribution(destination,session,candidates,frames)
    else:
        sides=case.sides(session.sim,frames)
        distances=[float(np.linalg.norm(p[:2]-session.sim.data.xpos[
            session.sim.model.body(session.config.target).id][:2])) for _,_,p in sides]
        clearances=[case.clearance(session.sim,p) for _,_,p in sides]
        info=diagnostics.furniture_sides(destination,session,frames,sides,distances,clearances,
                                        candidates)
    return {'view':name,'path':str(destination) if info.get('available') else None,'diagnostic':True,
            'stage':stage,'vla_input':False,'schematic':True,**info}


def parse_decision(raw, observation):
    try: decision=json.loads(raw) if isinstance(raw,str) else copy.deepcopy(raw)
    except (ValueError,TypeError) as exc: raise ValueError('bad_decision_json') from exc
    if not isinstance(decision,dict) or not {'action','revision','observation_id'} <= set(decision): raise ValueError('invalid_decision_schema')
    if type(decision['revision']) is not int or not isinstance(decision['observation_id'],str): raise ValueError('invalid_decision_identity')
    if decision['revision']!=observation['revision'] or decision['observation_id']!=observation['observation_id']: raise ValueError('stale_decision')
    if not isinstance(decision['action'],str): raise ValueError('invalid_decision_action')
    action=decision['action']; basic={'action','revision','observation_id'}
    if action in ('select','repair'):
        if set(decision)!=basic|{'candidate_id'}: raise ValueError('invalid_selection_schema')
        ids={x['candidate_id'] for x in observation['candidates']}
        if not isinstance(decision['candidate_id'],str) or decision['candidate_id'] not in ids: raise ValueError('unknown_candidate_id')
    elif action in ('rank','shortlist'):
        # Agent-side semantic ordering: a permutation or subset of program-enumerated candidates,
        # never a number and never a new candidate.
        if set(decision)!=basic|{'candidate_ids'} or not isinstance(decision['candidate_ids'],list) \
                or not decision['candidate_ids'] or not all(isinstance(x,str) for x in decision['candidate_ids']):
            raise ValueError('invalid_candidate_ranking')
        ids={x['candidate_id'] for x in observation['candidates']}
        if len(set(decision['candidate_ids']))!=len(decision['candidate_ids']) or set(decision['candidate_ids'])-ids:
            raise ValueError('unknown_candidate_id')
        if action=='rank' and set(decision['candidate_ids'])!=ids:
            raise ValueError('ranking_must_cover_all_candidates')
    elif action=='request_views':
        allowed=set(BASE_VIEWS)|set(CASE_VIEWS.get(observation.get('case_type'),()))
        if set(decision)!=basic|{'views'} or not isinstance(decision['views'],list) or not decision['views'] or not all(isinstance(v,str) for v in decision['views']) or set(decision['views'])-allowed:
            raise ValueError('invalid_view_request')
    elif action=='query_geometry':
        if set(decision)!=basic|{'region_id'} or decision['region_id'] not in {r['region_id'] for r in observation['regions']}:
            raise ValueError('invalid_geometry_query')
    elif action=='abandon':
        if set(decision)!=basic|{'reason'} or not isinstance(decision['reason'],str): raise ValueError('invalid_abandon_schema')
    else: raise ValueError('operation_not_permitted')
    return decision


class DecisionGateway:
    def __init__(self, backend, *, budget, timeout_s=30, path=None, purposes=None):
        self.backend,self.timeout_s,self.path=backend,timeout_s,Path(path) if path else None
        self.calls=0; self.failed=set()
        # Budget by purpose: `select` and `rank`/`shortlist` are counted separately so a
        # conversational loop cannot silently spend the selection budget.
        self.budget=VisionCallBudget(purposes or {'selection':budget,'ranking':budget})
        self.purposes={}

    def _consume(self, purpose):
        self.budget.consume(purpose); self.purposes[purpose]=self.purposes.get(purpose,0)+1

    def decide(self, observation):
        index=self.calls; self.calls+=1; start=time.monotonic(); out=queue.Queue(maxsize=1)
        log={'call':index,'source':'model' if self.backend else 'unavailable','observation_id':observation['observation_id']}
        def worker():
            try: out.put((True,self.backend(copy.deepcopy(observation))))
            except Exception as exc: out.put((False,str(exc)))
        try:
            if not self.backend: raise RuntimeError('vision_backend_unavailable')
            threading.Thread(target=worker,daemon=True).start()
            try: ok,raw=out.get(timeout=self.timeout_s)
            except queue.Empty as exc: raise TimeoutError('agent_timeout') from exc
            log['response_repr']=repr(raw)
            if not ok: raise RuntimeError(raw)
            decision=parse_decision(raw,observation)
            purpose='ranking' if decision['action'] in ('rank','shortlist') else 'selection'
            self._consume(purpose)
            log['purpose']=purpose
            if decision.get('candidate_id') in self.failed: raise ValueError('repeated_failed_candidate')
            log.update(status='accepted',decision=decision)
            return decision
        except Exception as exc:
            log.update(status='rejected',error=str(exc)); raise
        finally:
            log['wall_time_s']=time.monotonic()-start
            log['budget_remaining']=self.budget.remaining()
            if self.path: write_json(self.path/f'{index:04d}.json',log)
