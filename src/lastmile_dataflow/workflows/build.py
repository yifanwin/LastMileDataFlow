"""Budgeted rule-first construction, immutable freeze and independent handoff."""
from dataclasses import asdict, replace
from pathlib import Path
import re
import time
import uuid

import numpy as np

from ..agents.protocol import observe, DecisionGateway
from ..construction.candidates import candidates
from ..construction.templates import pending_hypotheses
from ..config import TaskConfig
from ..io import digest, write_json, read_json, file_digest
from ..runtime.build_session import BuildSession
from ..runtime.simulation import Simulation


class RequestBudget:
    """Shared across candidates/houses; a new build never resets request accounting."""
    def __init__(self, budget):
        self.limits=budget; self.used={'candidates':0,'edits':0,'repairs':0,'agent_calls':0}
        self.start=time.monotonic()

    def consume(self,key,count=1):
        if time.monotonic()-self.start>self.limits.timeout_s or self.used[key]+count>getattr(self.limits,key):
            raise RuntimeError(f'budget_exhausted:{key}')
        self.used[key]+=count


def run_build(source,robot_config,config,collection,*,build_id=None,images=True,regression=True,backend=None,request_budget=None,initial_frozen_dir=None):
    build_id=build_id or 'build-'+uuid.uuid4().hex[:12]
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',build_id): raise ValueError('unsafe build id')
    root=Path(collection.output_dir); path=root/'builds'/build_id; path.mkdir(parents=True,exist_ok=False)
    write_json(path/'config.json',{'build':asdict(config),'robot':asdict(robot_config),'collection':asdict(collection),'initial_frozen_dir':str(initial_frozen_dir) if initial_frozen_dir else None})
    budget=request_budget or RequestBudget(config.budget)
    started=time.monotonic(); session=None; frozen=None
    result={'schema_version':'2.0','build_id':build_id,'status':'failed','decision_source':'rule',
            'results':{'scene_validity':{'status':'unknown'},'build_requirements':{'status':'unknown'},
                       'case_condition':{'status':'unknown','reason':'requires_phase3_robot_trials'},
                       'task_completion':{'status':'unknown','reason':'no_task_execution'}}}
    observations=0
    def observation(cs,stage='settled',views=None):
        nonlocal observations
        packet=observe(session,cs,path/'observations'/f'{observations:04d}',stage=stage,images=images,views=views)
        observations+=1; return packet
    try:
        budget.consume('candidates')
        session=BuildSession(source,robot_config,config,collection,path=path,initial_frozen_dir=initial_frozen_dir)
        session.deadline=min(session.deadline,budget.start+budget.limits.timeout_s)
        session.record_images=images
        write_json(path/'initialization.json',session.initialization)
        observation([],stage='before_edit')
        if config.initial_operations:
            budget.consume('edits',len(config.initial_operations))
            entry=session.transact(config.initial_operations,revision=session.revision,reason='configured_group')
            if entry['status']!='committed': raise ValueError('initial_operations_failed')
        else:
            session.settle()
        # Track untouched assets across all transactions; revisions invalidate all previous checks.
        check=session.validate()
        write_json(path/'checks'/'initial.json',check)
        failed=set(); gateway=DecisionGateway(backend,budget=config.budget.agent_calls,path=path/'decisions')
        while not check['valid']:
            cs=[c for c in candidates(session) if c['candidate_id'] not in failed]
            cs=cs[:max(0,config.budget.candidates-budget.used['candidates'])]
            if not cs: raise RuntimeError('no_new_qualified_candidates')
            packet=observation(cs)
            if backend:
                while True:
                    budget.consume('agent_calls'); decision=gateway.decide(packet)
                    result['decision_source']='model'
                    if decision['action']=='abandon': raise RuntimeError('agent_abandoned:'+decision['reason'])
                    if decision['action'] in ('request_views','query_geometry'):
                        if decision['action']=='request_views' and not images: raise RuntimeError('requested_images_unavailable')
                        packet=observation(cs,views=decision.get('views')); continue
                    choice=next(c for c in cs if c['candidate_id']==decision['candidate_id']); break
            else:
                choice=cs[0]
                write_json(path/'decisions'/f'rule-{session.revision:04d}.json',{'source':'rule','revision':session.revision,'observation_id':packet['observation_id'],'candidate_id':choice['candidate_id']})
            budget.consume('candidates'); budget.consume('edits',len(choice['operations']))
            entry=session.transact(choice['operations'],revision=choice['revision'],source='model' if backend else 'rule')
            if entry['status']!='committed':
                failed.add(choice['candidate_id']); gateway.failed.add(choice['candidate_id']); budget.consume('repairs')
                # Rollback cleared the window: re-settle restored state, then recompute facts.
                session.settle()
            check=session.validate()
            write_json(path/'checks'/f'revision-{session.revision:04d}.json',check)
        observation([])
        # Fresh full-scene checks before freeze; stable IDs cover dynamic state and topology.
        model_id=session.model_id; checkpoint_id=session.checkpoint_id
        before_state=session.sim.state_vector().copy(); before_scene=session.sim.observe_state()
        version_dir=root/'scene_versions'/digest({'model_id':model_id,'checkpoint_id':checkpoint_id,'build_id':build_id})
        version=session.freeze(version_dir,build_id=build_id); frozen=version_dir
        restored=Simulation.from_snapshot(version_dir,robot_config,target=config.target)
        try:
            exact=bool(np.array_equal(before_state,restored.state_vector()) and before_scene==restored.observe_state())
            if not exact: raise ValueError('independent_restore_mismatch')
        finally: restored.close()
        write_json(path/'handoff.json',{'scene_dir':str(version_dir.resolve()),'independent_restore_exact':True})
        candidate={'schema_version':'2.0','build_id':build_id,'task_id':config.task_id,'case_type':config.case_type,
                   'target':config.target,'operation':config.operation,'robot_initial_state':before_scene['robot'],
                   'scene_version_id':version['version_id'],'model_id':model_id,'checkpoint_id':checkpoint_id,
                   'scene_dir':str(version_dir.resolve()),'requirements':check['requirements'],
                   'pending_hypotheses':pending_hypotheses(config.case_type),
                   'phase3_protocol':{'must_use_independent_attempt':True,'fixed_base_trial_required':config.case_type in ('case1','case1.5'),
                                      'task_success_evaluator':'required_not_implemented_in_phase2'},
                   'results':{'scene_validity':{'status':'valid','scope':'placement-v2_conservative_planar_support'},
                              'build_requirements':{'status':'pass'},'case_condition':result['results']['case_condition'],
                              'task_completion':result['results']['task_completion']}}
        write_json(path/'task_candidate.json',candidate)
        result['results']=candidate['results']; result['status']='candidate_ready'
        if regression:
            from ..runtime.runner import run_attempt,audit_attempt
            task=TaskConfig(task_id=config.task_id+'-handoff',case_type=config.case_type,target=config.target)
            short=replace(collection,max_steps=min(collection.max_steps,6))
            attempt=run_attempt(None,robot_config,task,short,frozen_dir=version_dir,attempt_id=build_id+'-handoff',strategy='phase2_handoff_short_action')
            audit=audit_attempt(attempt); attempt_result=read_json(attempt/'result.json')
            write_json(path/'regression.json',{'attempt':str(attempt),'audit':audit,'status':attempt_result['status']})
            if not audit['valid'] or attempt_result['status']!='execution_complete':
                result['status']='handoff_failed'
        else:
            result['regression']='not_run'; result['status']='candidate_ready_regression_pending'
    except KeyboardInterrupt:
        result.update(status='interrupted',error='operator_interrupted_build')
    except Exception as exc:
        result.update(error_type=type(exc).__name__,error=str(exc))
        if session and not session.closed:
            write_json(path/'failure_state.json',session.sim.observe_state())
            if session.last_check:
                result['last_check']=session.last_check
                result['results']['scene_validity']={'status':'valid' if session.last_check['scene_valid'] else 'invalid','scope':config.protocol.version}
                result['results']['build_requirements']={'status':'pass' if all(r['status']=='pass' for r in session.last_check['requirements']) else 'fail'}
        if isinstance(exc,TimeoutError) or 'budget_exhausted' in str(exc): result['status']='budget_exhausted'
    finally:
        if session: session.close()
        result['cost']={'wall_time_s':time.monotonic()-started,'request_cumulative':budget.used,'observations':observations,
                        'simulated_s':session.simulated_s if session else 0}
        if frozen: result['frozen_scene_dir']=str(frozen)
        write_json(path/'result.json',result)
        write_json(path/'artifacts.json',{'files_sha256':{str(p.relative_to(path)):file_digest(p) for p in path.rglob('*') if p.is_file() and p.name!='artifacts.json'}})
    return path


def build_request(sources,robot_config,config,collection,**options):
    budget=RequestBudget(config.budget); paths=[]
    isolated=options.pop('isolated',True)
    for item in sources:
        source, selected = item if isinstance(item,tuple) else (item,config)
        runner=supervised_build if isolated else run_build
        path=runner(source,robot_config,selected,collection,request_budget=budget,**options); paths.append(path)
        result=read_json(path/'result.json')
        if result['status'] in ('candidate_ready','candidate_ready_regression_pending','budget_exhausted','interrupted'): break
    return paths


def record_feedback(build_path,classification,evidence,*,new_build_id=None):
    actions={'scene_invalid':'repair_or_change_candidate','success_without_expected_difficulty':'retain_and_reclassify',
             'valid_unsolved':'retain_unsolved_do_not_remove_obstacles','infrastructure_failure':'retry_infrastructure_without_scene_edits'}
    if classification not in actions or not isinstance(evidence,dict) or not evidence: raise ValueError('invalid downstream feedback')
    candidate=read_json(Path(build_path)/'task_candidate.json')
    feedback={'schema_version':'2.0','parent_build_id':candidate['build_id'],'scene_version_id':candidate['scene_version_id'],
              'classification':classification,'action':actions[classification],'evidence':evidence,'new_build_id':new_build_id}
    # Append-only sibling records, never modify frozen/collected artifacts.
    root=Path(build_path).parent.parent/'feedback'; path=root/(uuid.uuid4().hex+'.json')
    write_json(path,feedback); return path


def _build_worker(connection, source, robot, config, collection, budget, options):
    """Separate process so cold NAS/model compilation cannot defeat CLI timeout."""
    try:
        path=run_build(source,robot,config,collection,request_budget=budget,**options)
        connection.send({'path':str(path)})
    except BaseException as exc:
        connection.send({'error':str(exc),'type':type(exc).__name__})
    finally:
        connection.close()


def supervised_build(source,robot_config,config,collection,*,request_budget=None,**options):
    import multiprocessing
    budget=request_budget or RequestBudget(config.budget)
    if options.get('backend') is not None:
        raise ValueError('in-process vision callbacks require run_build; supervised CLI is rule-only')
    build_id=options.get('build_id') or 'build-'+uuid.uuid4().hex[:12]
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',build_id): raise ValueError('unsafe build id')
    options['build_id']=build_id
    path=Path(collection.output_dir)/'builds'/build_id
    if path.exists(): raise FileExistsError(path)
    remaining=budget.start+budget.limits.timeout_s-time.monotonic()
    ctx=multiprocessing.get_context('spawn')
    reader,writer=ctx.Pipe(duplex=False)
    worker=ctx.Process(target=_build_worker,args=(writer,source,robot_config,config,collection,budget,options))
    timed_out=remaining<=0
    started=False
    try:
        if not timed_out:
            worker.start(); started=True; writer.close()
            worker.join(max(0.,remaining))
            timed_out=worker.is_alive()
            if timed_out:
                worker.terminate(); worker.join(2)
                if worker.is_alive(): worker.kill(); worker.join(2)
            elif reader.poll():
                message=reader.recv()
                if 'error' in message: raise RuntimeError(f"build worker {message['type']}: {message['error']}")
                result=read_json(path/'result.json')
                budget.used.update(result['cost']['request_cumulative'])
                return Path(message['path'])
        path.mkdir(parents=True,exist_ok=True)
        result={'schema_version':'2.0','build_id':build_id,'status':'budget_exhausted' if timed_out else 'infrastructure_error',
                'error':'hard_wall_clock_budget_exhausted' if timed_out else 'worker_exited_without_result',
                'results':{k:{'status':'unknown'} for k in ('scene_validity','build_requirements','case_condition','task_completion')},
                'cost':{'wall_time_s':time.monotonic()-budget.start,'request_cumulative':budget.used.copy(),'accounting_incomplete':timed_out},
                'supervisor':{'isolated_process':True,'native_worker_reaped':not started or not worker.is_alive()}}
        write_json(path/'result.json',result)
        write_json(path/'artifacts.json',{'files_sha256':{str(p.relative_to(path)):file_digest(p) for p in path.rglob('*') if p.is_file() and p.name!='artifacts.json'}})
        return path
    finally:
        reader.close(); writer.close()
        if started and worker.is_alive(): worker.kill(); worker.join(2)
        if started and not worker.is_alive(): worker.close()
