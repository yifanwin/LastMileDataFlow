"""Program-first unedited→edited routing, common comparison, optional review, freeze.

Backends supply measured evidence, not verdicts. No legacy Agent construction calls.
"""
import copy
from pathlib import Path
import re
import time
from uuid import uuid4
from ..io import write_json,read_json,digest,file_digest,validate_case_task
from ..stations.start_selection import start_pairs
from ..validation.comparison import compare_pair


def run_case_factory(sources,spec,backend,output,*,run_id=None,max_pairs=4,quota=5,execute=True,_reservation=None):
    run_id=run_id or 'factory-'+uuid4().hex[:12]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',run_id): raise ValueError('unsafe run ID')
    if type(max_pairs) is not int or max_pairs<=0 or type(quota) is not int or quota<=0 or type(execute) is not bool:
        raise ValueError('invalid factory budget/quota')
    sources=list(sources)
    if not sources: raise ValueError('at least one pure scene required')
    path=Path(output)/'case_factory'/run_id
    if _reservation is None:
        path.mkdir(parents=True,exist_ok=False)
    elif read_json(path/'reservation.json').get('token')!=_reservation or (path/'inputs.json').exists():
        raise FileExistsError('invalid or reused factory reservation')
    started=time.monotonic()
    funnel={branch:{key:0 for key in ('candidates','station_map_success','start_selected','L1','L2','review_kept','frozen')}
            for branch in ('unedited','edited')}
    tasks=[]; failures=[]; events=[]; l2_count=0
    write_json(path/'inputs.json',{'schema_version':'case-factory-v1','spec':spec.to_dict(),'spec_digest':spec.spec_digest,
        'sources':[s.__dict__ for s in sources],'quota':quota,'max_pairs_per_candidate':max_pairs,'strict_execution_requested':execute,
        'source_policy':'pure_scenes_no_benchmark_tasks','review_policy':getattr(backend,'review_config',None),
        'backend':backend.frozen_inputs() if hasattr(backend,'frozen_inputs') else {'scope':'injected_test_backend'}})
    def save():
        write_json(path/'funnel.json',funnel); write_json(path/'failures.json',failures); write_json(path/'tasks.json',tasks)
    def process(candidate,branch,directory):
        nonlocal l2_count
        funnel[branch]['candidates']+=1
        rows=backend.station_map(candidate,spec,directory/'station_map')
        if any(r['plan']=='success' for r in rows): funnel[branch]['station_map_success']+=1
        pairs=start_pairs(spec,rows)
        # Same-edge reach cases are a separate output class, never a case1 success.
        if spec.case_type=='case1':
            from ..construction.case_spec import CaseSpec
            raw=spec.to_dict(); raw.update(case_type='case1-S',move_types=['same_edge']); same_spec=CaseSpec.from_dict(raw)
            pairs+=start_pairs(same_spec,rows)
        if pairs: funnel[branch]['start_selected']+=1
        accepted=False
        for i,(s0,s1) in enumerate(pairs[:max_pairs]):
            pair_path=directory/f'pair-{i:04d}'
            packet=backend.pair_packet(candidate,spec,copy.deepcopy(s0),copy.deepcopy(s1),pair_path)
            verdict=compare_pair(spec,packet); write_json(pair_path/'comparison_L1.json',{'packet':packet,'verdict':verdict})
            if verdict['status']!='pass': continue
            funnel[branch]['L1']+=1
            if execute:
                packet=backend.execute_pair(candidate,packet,pair_path/'execution')
                verdict=compare_pair(spec,packet)
            write_json(pair_path/'comparison.json',{'packet':packet,'verdict':verdict})
            if verdict['improvement_level']=='L2': funnel[branch]['L2']+=1
            if verdict['status']!='pass': continue
            review=backend.review(candidate,packet,pair_path/'review')
            write_json(pair_path/'review_result.json',review)
            if review['conclusion']=='implausible': continue
            funnel[branch]['review_kept']+=1
            if verdict['improvement_level']=='L2' and verdict['case_type']==spec.case_type: l2_count+=1
            scene=Path(candidate['snapshot']); checks=read_json(scene/'checksums.json')
            if read_json(scene/'version.json').get('version_id')!=packet['C0']['policy']['scene_version']:
                raise ValueError('comparison/scene version mismatch')
            for name,sha in checks.items():
                file=scene/name
                if not file.resolve().is_relative_to(scene.resolve()) or file_digest(file)!=sha: raise ValueError('scene checksum mismatch')
            record={'schema_version':'case-task-v1','task_id':f'{run_id}-{len(tasks):06d}',
                'case_type':verdict['case_type'],'requested_case_type':spec.case_type,'construction_branch':branch,
                'edits':candidate.get('edits',[]),'move_type':verdict['move_type'],'attribution':verdict['attribution'],
                'improvement_level':verdict['improvement_level'],'construction_validity':verdict['construction_validity'],
                'case_condition':verdict['case_condition'],'task_success':verdict['task_success'],
                'S0':packet['C0'],'S1':packet['C1'],'station_map':str((directory/'station_map/stations.json').resolve()),
                'comparison_evidence':str((pair_path/'comparison.json').resolve()),'review':review,
                'manual_review':review.get('manual_review',False),'scene_dir':str(scene.resolve()),
                'scene_checksums':checks,'spec_digest':spec.spec_digest,
                'delivery_status':'L2_pending_navigation' if verdict['improvement_level']=='L2' else 'L1_not_formal_delivery',
                'navigation_success':'unknown'}
            record['record_digest']=digest(record); validate_case_task(record); tasks.append(record)
            write_json(pair_path/'task.json',record); funnel[branch]['frozen']+=1; accepted |= verdict['case_type']==spec.case_type; save()
        return accepted
    try:
        for number,source in enumerate(sources):
            if l2_count>=quota: break
            source_path=path/f'source-{number:05d}'
            try:
                candidates=backend.prepare(source,spec,source_path/'original')
                for index,candidate in enumerate(candidates):
                    if l2_count>=quota: break
                    directory=source_path/f'candidate-{index:05d}'
                    try:
                        success=process(candidate,'unedited',directory/'unedited')
                        if not success:
                            events.append({'candidate':candidate.get('candidate_id'),'event':'route_to_edited','original_snapshot':candidate['snapshot']})
                            for j,edited in enumerate(backend.edited_candidates(copy.deepcopy(candidate),spec,directory/'edited_proposals')):
                                process(edited,'edited',directory/f'edited-{j:04d}')
                    except Exception as exc:
                        failures.append({'source':source.scene_id,'candidate':candidate.get('candidate_id'),
                                         'status':'unknown','error_type':type(exc).__name__,'reason':str(exc)[:250]})
                    finally: save()
            except Exception as exc:
                failures.append({'source':source.scene_id,'status':'unknown','error_type':type(exc).__name__,
                                 'reason':str(exc)[:250],'details':getattr(exc,'details',None),
                                 'scene_validity':'invalid' if type(exc).__name__=='InitializationError' else 'unknown',
                                 'case_condition':'unknown','task_success':'unknown'})
            finally: backend.close(); save()
    finally: backend.close()
    write_json(path/'routing.json',events)
    write_json(path/'summary.json',{'schema_version':'case-factory-v1','run_id':run_id,
        'status':'quota_reached' if l2_count>=quota else 'incomplete','L2_requested_case_count':l2_count,'quota':quota,
        'task_count':len(tasks),'failure_count':len(failures),'funnel':funnel,'wall_time_s':time.monotonic()-started,
        'formal_dataset_complete':False,'navigation_success':'unknown',
        'scope':'native case1 path; other cases require counterfactual/edit transaction adapters'})
    return path


def _factory_worker(connection,sources,spec,robot,collection,config,options):
    try:
        from ..workflows.factory_backend import NativeFactoryBackend,FactoryDependencyError
        from ..agents.case_gateway import HTTPCaseBackend
        reviewer=HTTPCaseBackend(options['api_settings'],provider=options['provider'],model=options['model']) if options['api_settings'] else None
        backend=NativeFactoryBackend(robot,collection,config,reviewer=reviewer,review_config=options['review_config'])
        if spec.case_type not in ('case1','case1-S'):
            raise FactoryDependencyError('native counterfactual/edit adapters not connected for '+spec.case_type)
        path=run_case_factory(sources,spec,backend,options['output'],run_id=options['run_id'],max_pairs=options['max_pairs'],quota=options['quota'],execute=options['execute'],_reservation=options['reservation'])
        connection.send({'path':str(path)})
    except BaseException as exc:
        connection.send({'error_type':type(exc).__name__,'reason':str(exc)[:250],
                         'status':'dependencies_missing' if type(exc).__name__=='FactoryDependencyError' else 'infrastructure_error'})
    finally: connection.close()


def supervised_case_factory(sources,spec,robot,collection,config,*,output,run_id=None,max_pairs=4,quota=5,
                            execute=True,api_settings=None,provider=None,model=None,review_config=None):
    """Hard run deadline: hung CUDA/rendering cannot run forever or create false failures."""
    import multiprocessing
    sources=list(sources)
    if not sources: raise ValueError('at least one pure scene required')
    run_id=run_id or 'factory-'+uuid4().hex[:12]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',run_id): raise ValueError('unsafe run ID')
    path=Path(output)/'case_factory'/run_id
    path.mkdir(parents=True,exist_ok=False)
    reservation=uuid4().hex
    write_json(path/'reservation.json',{'token':reservation,'run_id':run_id})
    from dataclasses import asdict
    write_json(path/'launch.json',{'schema_version':'case-factory-launch-v1','sources':[x.__dict__ for x in sources],
        'spec':spec.to_dict(),'robot':asdict(robot),'collection':asdict(collection),'factory':config,
        'review_config':review_config,'external_review_requested':api_settings is not None,
        'run_id':run_id,'quota':quota,'max_pairs':max_pairs,'execute':execute})
    options=dict(output=output,run_id=run_id,max_pairs=max_pairs,quota=quota,execute=execute,
                 api_settings=api_settings,provider=provider,model=model,review_config=review_config,reservation=reservation)
    ctx=multiprocessing.get_context('spawn'); reader,writer=ctx.Pipe(duplex=False)
    process=ctx.Process(target=_factory_worker,args=(writer,sources,spec,robot,collection,config,options))
    try:
        process.start(); writer.close(); process.join(config['timeout_s'])
        if process.is_alive():
            process.terminate(); process.join(3)
            if process.is_alive(): process.kill(); process.join(3)
            path.mkdir(parents=True,exist_ok=True)
            write_json(path/'interruption.json',{'status':'budget_exhausted','worker_reaped':not process.is_alive(),
                       'partial_attempts':'incomplete_not_physical_failure'})
            if not (path/'summary.json').exists():
                write_json(path/'summary.json',{'status':'budget_exhausted','formal_dataset_complete':False})
            return path
        try:
            result=reader.recv() if reader.poll() else {'error_type':'WorkerExit','reason':'no_result'}
        except EOFError:
            result={'error_type':'WorkerExit','reason':'pipe_closed_without_result'}
        if 'path' in result: return Path(result['path'])
        path.mkdir(parents=True,exist_ok=True)
        write_json(path/'worker_error.json',result)
        if not (path/'summary.json').exists():
            write_json(path/'summary.json',{'status':'infrastructure_error','formal_dataset_complete':False,**result})
        return path
    finally:
        if process.is_alive(): process.kill(); process.join(3)
        reader.close(); writer.close(); process.close()
