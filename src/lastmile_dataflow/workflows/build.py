"""Budgeted rule-first construction, immutable freeze and independent handoff.

【编排层】这个文件把阶段二的各领域模块串成一个流程，自身不含几何/物理算法。

【要理解的三件事】
  1. 预算贯穿整个 request：RequestBudget 在多个房屋之间共享，换房屋不会重置计数。
     这是为了防止“多试几所房子总有能过的”式作弊。
  2. 规则优先：默认没有 AI 后端，直接取 cost 最小的候选；
     若传入 backend，才走 DecisionGateway（但仍受严格协议约束）。
  3. 结果状态是有限枚举：candidate_ready / candidate_ready_regression_pending /
     handoff_failed / budget_exhausted / interrupted / failed。
     注意 case_condition 与 task_completion 始终是 unknown——阶段二不管任务成功。
"""
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
    """Shared across candidates/houses; a new build never resets request accounting.

    四类预算：候选数、编辑次数、修复次数、Agent 调用数；外加一个总墙钟期限。
    任何一次 consume 超限就抛 RuntimeError('budget_exhausted:...')。
    """

    def __init__(self, budget):
        self.limits=budget; self.used={'candidates':0,'edits':0,'repairs':0,'agent_calls':0}
        self.start=time.monotonic()

    def consume(self,key,count=1):
        if time.monotonic()-self.start>self.limits.timeout_s or self.used[key]+count>getattr(self.limits,key):
            raise RuntimeError(f'budget_exhausted:{key}')
        self.used[key]+=count


def run_build(source,robot_config,config,collection,*,build_id=None,images=True,regression=True,backend=None,request_budget=None,initial_frozen_dir=None):
    """在**进程内**执行一次构建（协作式检查期限）。需要硬截止请用 supervised_build/build_request。

    主循环逻辑见文件顶部流程图：初始化 → (validate 不通过就反复候选-编辑-静置-验证) →
    冻结 → 独立恢复逐字节比对 → 写 task_candidate → 可选短动作交接回归。
    """
    build_id=build_id or 'build-'+uuid.uuid4().hex[:12]
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',build_id): raise ValueError('unsafe build id')
    root=Path(collection.output_dir); path=root/'builds'/build_id; path.mkdir(parents=True,exist_ok=False)
    write_json(path/'config.json',{'build':asdict(config),'robot':asdict(robot_config),'collection':asdict(collection),'initial_frozen_dir':str(initial_frozen_dir) if initial_frozen_dir else None})
    budget=request_budget or RequestBudget(config.budget)
    started=time.monotonic(); session=None; frozen=None
    # 默认“失败但未知”：只有真正走到 candidate_ready 才会改写。三个结论先全设 unknown。
    result={'schema_version':'2.0','build_id':build_id,'status':'failed','decision_source':'rule',
            'results':{'scene_validity':{'status':'unknown'},'build_requirements':{'status':'unknown'},
                       'case_condition':{'status':'unknown','reason':'requires_phase3_robot_trials'},
                       'task_completion':{'status':'unknown','reason':'no_task_execution'}}}
    observations=0
    def observation(cs,stage='settled',views=None):
        """按序号建观察包目录（0000,0001,...），供决策与事后复查。"""
        nonlocal observations
        packet=observe(session,cs,path/'observations'/f'{observations:04d}',stage=stage,images=images,views=views)
        observations+=1; return packet
    try:
        budget.consume('candidates')
        session=BuildSession(source,robot_config,config,collection,path=path,initial_frozen_dir=initial_frozen_dir)
        # 构建期限取“会话自身期限”和“request 总期限”中较早的那个。
        session.deadline=min(session.deadline,budget.start+budget.limits.timeout_s)
        session.record_images=images
        write_json(path/'initialization.json',session.initialization)
        # 机器人初态不合法时不做“修复目标”式的乱试，直接失败（避免掩盖场景问题）。
        if session.initialization['status'] != 'valid': raise ValueError('invalid_robot_initialization_no_target_repair')
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
        # ★ 主循环：只要检查不通过就继续找候选，直到通过或预算/候选耗尽。
        while not check['valid']:
            cs=[c for c in candidates(session) if c['candidate_id'] not in failed]
            cs=cs[:max(0,config.budget.candidates-budget.used['candidates'])]
            if not cs: raise RuntimeError('no_new_qualified_candidates')
            packet=observation(cs)
            if backend:
                # 有模型后端：走严格决策协议；每轮都要重算 observation 身份，过期决策直接拒绝。
                while True:
                    budget.consume('agent_calls'); decision=gateway.decide(packet)
                    result['decision_source']='model'
                    if decision['action']=='abandon': raise RuntimeError('agent_abandoned:'+decision['reason'])
                    if decision['action'] in ('request_views','query_geometry'):
                        if decision['action']=='request_views' and not images: raise RuntimeError('requested_images_unavailable')
                        packet=observation(cs,views=decision.get('views')); continue
                    choice=next(c for c in cs if c['candidate_id']==decision['candidate_id']); break
            else:
                # 规则路径：直接取 cost 最小的候选，并记录“这是规则决定，不是模型复查”。
                choice=cs[0]
                write_json(path/'decisions'/f'rule-{session.revision:04d}.json',{'source':'rule','revision':session.revision,'observation_id':packet['observation_id'],'candidate_id':choice['candidate_id']})
            budget.consume('candidates'); budget.consume('edits',len(choice['operations']))
            entry=session.transact(choice['operations'],revision=choice['revision'],source='model' if backend else 'rule')
            if entry['status']!='committed':
                # 候选失败：加入黑名单，消耗一次修复预算，并重新静置（回滚已清空窗口）。
                failed.add(choice['candidate_id']); gateway.failed.add(choice['candidate_id']); budget.consume('repairs')
                # Rollback cleared the window: re-settle restored state, then recompute facts.
                session.settle()
            check=session.validate()
            write_json(path/'checks'/f'revision-{session.revision:04d}.json',check)
        observation([])
        # Fresh full-scene checks before freeze; stable IDs cover dynamic state and topology.
        model_id=session.model_id; checkpoint_id=session.checkpoint_id
        before_state=session.sim.state_vector().copy(); before_scene=session.sim.observe_state()
        # 场景版本目录名由“模型身份 + 检查点身份 + 构建 ID”的摘要决定 → 内容寻址。
        version_dir=root/'scene_versions'/digest({'model_id':model_id,'checkpoint_id':checkpoint_id,'build_id':build_id})
        version=session.freeze(version_dir,build_id=build_id); frozen=version_dir
        # ★ 关键验证：从冻结目录独立恢复，状态与现场必须**逐字节相等**，否则拒绝交付。
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
            # 交接回归：用这个冻结场景跑一次 6 步短动作，证明“场景能被阶段一正常加载执行”。
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
        # 失败也要尽力给出“最后一次检查”的结论，供后续反馈使用。
        result.update(error_type=type(exc).__name__,error=str(exc))
        if session and not session.closed:
            write_json(path/'failure_state.json',session.sim.observe_state())
            if session.last_check:
                result['last_check']=session.last_check
                result['results']['scene_validity']={'status':'valid' if session.last_check['scene_valid'] else 'invalid','scope':config.protocol.version}
                result['results']['build_requirements']={'status':'pass' if all(r['status']=='pass' for r in session.last_check['requirements']) else 'fail'}
        if isinstance(exc,TimeoutError) or 'budget_exhausted' in str(exc): result['status']='budget_exhausted'
    finally:
        # 无论成败都写成本与全文件摘要。
        if session: session.close()
        result['cost']={'wall_time_s':time.monotonic()-started,'request_cumulative':budget.used,'observations':observations,
                        'simulated_s':session.simulated_s if session else 0}
        if frozen: result['frozen_scene_dir']=str(frozen)
        write_json(path/'result.json',result)
        write_json(path/'artifacts.json',{'files_sha256':{str(p.relative_to(path)):file_digest(p) for p in path.rglob('*') if p.is_file() and p.name!='artifacts.json'}})
    return path


def build_request(sources,robot_config,config,collection,**options):
    """对多个来源依次构建，共用一个 RequestBudget；一旦某个来源成功（或预算耗尽）就停止。

    这样设计是为了“尽力找一个能构建成功的场景，但不靠无限重试堆成功”。
    """
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
    """追加下游（阶段三）反馈。**只追加新文件，绝不修改原 build 或冻结场景。**

    classification 五选一，且 case_verified 需要真实站位 run 的摘要与物理见证，
    不是调用方说了算——这里会重新审计 witness attempt。
    """
    actions={'case_verified':'retain_verified_case_and_evidence','scene_invalid':'repair_or_change_candidate','success_without_expected_difficulty':'retain_and_reclassify',
             'valid_unsolved':'retain_unsolved_do_not_remove_obstacles','infrastructure_failure':'retry_infrastructure_without_scene_edits'}
    if classification not in actions or not isinstance(evidence,dict) or not evidence: raise ValueError('invalid downstream feedback')
    candidate=read_json(Path(build_path)/'task_candidate.json')
    if classification=='case_verified':
        # 要求：summary 摘要匹配 + case 判定为 pass + 存在成功见证 + 每个见证都被独立审计通过。
        from ..validation.stations import audit_station_attempt
        run=Path(evidence.get('station_run',''))
        summary_path=run/'summary.json'
        if not summary_path.is_file() or file_digest(summary_path)!=evidence.get('summary_sha256'):
            raise ValueError('case_verified requires matching real station summary')
        summary=read_json(summary_path); verdict=summary.get('case_condition',{})
        if verdict.get('status')!='pass' or summary.get('scene_version')!=candidate['scene_version_id'] or not verdict.get('success_witnesses'):
            raise ValueError('case_verified requires physical success witnesses in this scene')
        for witness in verdict['success_witnesses']:
            audit=audit_station_attempt(witness)
            source=read_json(Path(witness)/'source.json'); result=read_json(Path(witness)/'result.json')
            if not audit['valid'] or result['results']['task_completion']['status']!='success' or source.get('scene_version')!=candidate['scene_version_id'] or source.get('target')!=candidate['target']:
                raise ValueError('case_verified witness is invalid or mismatched')
    feedback={'schema_version':'2.0','parent_build_id':candidate['build_id'],'scene_version_id':candidate['scene_version_id'],
              'classification':classification,'action':actions[classification],'evidence':evidence,'new_build_id':new_build_id}
    # Append-only sibling records, never modify frozen/collected artifacts.
    root=Path(build_path).parent.parent/'feedback'; path=root/(uuid.uuid4().hex+'.json')
    write_json(path,feedback); return path


def _build_worker(connection, source, robot, config, collection, budget, options):
    """Separate process so cold NAS/model compilation cannot defeat CLI timeout.

    在子进程里跑 run_build：这样即使原生模型加载卡住，主进程也能按墙钟强杀。
    """
    try:
        path=run_build(source,robot,config,collection,request_budget=budget,**options)
        connection.send({'path':str(path)})
    except BaseException as exc:
        connection.send({'error':str(exc),'type':type(exc).__name__})
    finally:
        connection.close()


def supervised_build(source,robot_config,config,collection,*,request_budget=None,**options):
    """隔离进程 + 硬墙钟期限的构建入口（CLI 默认走它）。

    超时/子进程异常退出时，不是“什么都不留”，而是写一份 status=budget_exhausted 或
    infrastructure_error 的结果，并注明部分证据，保持“不伪造终态”的原则。
    """
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
                # 先 terminate，2 秒还没死再 kill（原生库可能不响应 SIGTERM）。
                worker.terminate(); worker.join(2)
                if worker.is_alive(): worker.kill(); worker.join(2)
            elif reader.poll():
                message=reader.recv()
                if 'error' in message: raise RuntimeError(f"build worker {message['type']}: {message['error']}")
                result=read_json(path/'result.json')
                budget.used.update(result['cost']['request_cumulative'])   # 把子进程里的预算消耗同步回主进程
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
