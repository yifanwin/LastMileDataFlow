"""Optional vision gateway: version-bound finite decisions, no execution privileges."""
import copy
import json
from pathlib import Path
import queue
import threading
import time
import mujoco
import imageio.v2 as imageio
from ..io import digest, write_json


def observe(session, candidates, path, *, stage='settled', images=False, views=None):
    session.active()
    if stage not in ('before_edit','after_edit_unsettled','settled'): raise ValueError('unknown observation stage')
    path=Path(path); path.mkdir(parents=True,exist_ok=False)
    packet={'schema_version':'2.0','revision':session.revision,'model_id':session.model_id,
            'checkpoint_id':session.checkpoint_id,'stage':stage,'state':session.sim.observe_state(),
            'regions':[r.to_dict() for r in session.placements.values()], 'candidates':candidates,
            'requirements':session.last_check,'protected':session.config.protected,'images':[],
            'decision_source':'rule','not_vla_input':True}
    if images:
        renderer=mujoco.Renderer(session.sim.model,height=240,width=320)
        try:
            target=session.sim.data.xpos[session.sim.model.body(session.config.target).id]
            wanted=views or ['diagnostic_top','diagnostic_target','diagnostic_side','robot_head']
            for name in wanted:
                if name=='robot_head':
                    camera=session.sim.robot.camera_names['head_camera']; diagnostic=False
                else:
                    camera=mujoco.MjvCamera(); camera.lookat=target; camera.distance=1.8 if name=='diagnostic_top' else .7
                    camera.azimuth=90 if name=='diagnostic_side' else 0
                    camera.elevation=-89 if name=='diagnostic_top' else -15 if name=='diagnostic_side' else -35
                    diagnostic=True
                renderer.update_scene(session.sim.data,camera=camera)
                imageio.imwrite(path/f'{name}.png',renderer.render())
                packet['images'].append({'view':name,'path':str(path/f'{name}.png'),'diagnostic':diagnostic,'stage':stage,'vla_input':False})
        finally: renderer.close()
    packet['observation_id']=digest(packet)
    write_json(path/'observation.json',packet)
    return packet


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
    elif action=='request_views':
        if set(decision)!=basic|{'views'} or not isinstance(decision['views'],list) or not decision['views'] or not all(isinstance(v,str) for v in decision['views']) or set(decision['views'])-{'diagnostic_top','diagnostic_target','diagnostic_side','robot_head'}:
            raise ValueError('invalid_view_request')
    elif action=='query_geometry':
        if set(decision)!=basic|{'region_id'} or decision['region_id'] not in {r['region_id'] for r in observation['regions']}:
            raise ValueError('invalid_geometry_query')
    elif action=='abandon':
        if set(decision)!=basic|{'reason'} or not isinstance(decision['reason'],str): raise ValueError('invalid_abandon_schema')
    else: raise ValueError('operation_not_permitted')
    return decision


class DecisionGateway:
    def __init__(self, backend, *, budget, timeout_s=30, path=None):
        self.backend,self.budget,self.timeout_s,self.path=backend,budget,timeout_s,Path(path) if path else None
        self.calls=0; self.failed=set()

    def decide(self, observation):
        if self.calls>=self.budget: raise RuntimeError('agent_call_budget_exhausted')
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
            if decision.get('candidate_id') in self.failed: raise ValueError('repeated_failed_candidate')
            log.update(status='accepted',decision=decision)
            return decision
        except Exception as exc:
            log.update(status='rejected',error=str(exc)); raise
        finally:
            log['wall_time_s']=time.monotonic()-start
            if self.path: write_json(self.path/f'{index:04d}.json',log)
