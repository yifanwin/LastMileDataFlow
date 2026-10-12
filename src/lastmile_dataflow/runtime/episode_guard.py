"""Full-lifecycle episode isolation; shared resource failures are fail-stop.

Diagnostic sidecars never rewrite sealed attempts or invent execution evidence.
"""
from contextvars import ContextVar
from functools import wraps
import errno
import inspect
from pathlib import Path
import traceback
import uuid

from ..io import canonical, write_json, read_json

_SCOPE = ContextVar('episode_scope', default=None)
LOCAL_ERRORS = ('initialization_error', 'episode_error', 'recording_error')
_FATAL_ERRNOS = {errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.EACCES, errno.EPERM,
                 errno.EIO, errno.ENODEV, errno.EMFILE, errno.ENFILE, errno.ENOMEM}


def classify_episode_exception(exc, phase='execution'):
    message = str(exc).lower()
    if getattr(exc, 'shared_resource', False):
        status, category = 'infrastructure_error', 'SharedPolicyFailure'
    elif (isinstance(exc, MemoryError) or type(exc).__name__=='OutOfMemoryError') or (isinstance(exc, OSError) and exc.errno in _FATAL_ERRNOS):
        status, category = 'infrastructure_error', 'SharedResourceFailure'
    elif any(s in message for s in ('cuda out of memory', 'cuda error', 'device-side assert',
                                    'illegal memory access', 'context lost', 'egl_not_initialized',
                                    'valid opengl context has not been created', 'egl_bad_alloc',
                                    'egl_bad_context', 'cuda driver', 'cuda initialization', 'no cuda gpus are available')):
        status, category = 'infrastructure_error', 'SharedGPUFailure'
    else:
        status = 'initialization_error' if phase == 'initialize' else 'recording_error' if phase in ('finalize','recording') else 'episode_error'
        category = {'initialization_error':'EpisodeInitializationError','recording_error':'EpisodeRecordingError',
                    'episode_error':'EpisodeExecutionError'}[status]
    return {'status':status, 'failure_scope':'shared' if status=='infrastructure_error' else 'episode',
            'attribution':{'category':category,'error_type':type(exc).__name__,'message':str(exc),'phase':phase},
            'reason':type(exc).__name__+':'+str(exc)}


def episode_phase():
    return _SCOPE.get().phase if _SCOPE.get() is not None else 'execution'


def mark_episode_phase(phase):
    if _SCOPE.get() is not None: _SCOPE.get().phase = phase


def track_resource(resource, close=None):
    scope = _SCOPE.get()
    if scope is not None:
        scope.resources.append((resource, close or resource.close))
    return resource


def track_recorder(recorder):
    scope = _SCOPE.get()
    if scope is not None:
        scope.recorder = recorder
        scope.resources.append((recorder, recorder.abort_close))


def episode_diagnostic(root, attempt_id, detail, stage='lifecycle'):
    # Unique append-only sidecar, including failures before an attempt was created.
    path = Path(root)/'episode_errors'/f'{attempt_id}-{stage}-{uuid.uuid4().hex[:8]}.json'
    write_json(path, detail)  # If even minimal evidence cannot be saved, stop safely.
    return str(path)


class EpisodeScope:
    def __init__(self):
        self.phase='initialize'; self.resources=[]; self.recorder=None

    def cleanup(self):
        errors=[]
        for resource,close in reversed(self.resources):
            if getattr(resource,'closed',False) is True:
                continue
            try: close()
            except Exception as exc:
                errors.append(classify_episode_exception(exc,'finalize'))
        return errors


def execute_isolated_episode(function, *args, **kwargs):
    if _SCOPE.get() is not None:
        return function(*args, **kwargs)
    context=kwargs.pop('_episode_context',None)
    bound=context or inspect.signature(function).bind(*args,**kwargs).arguments
    root,task,station,attempt_id=(bound[k] for k in ('root','task','station','attempt_id'))
    scope=EpisodeScope(); token=_SCOPE.set(scope); caught=None
    try:
        try:
            result=function(*args,**kwargs)
            if not isinstance(result,dict) or result.get('station_id',station['station_id']) != station['station_id']:
                raise ValueError('invalid episode result identity')
            result={**result,'station_id':station['station_id']}
            canonical(result)  # Reject unserializable episode data before the shared ledger.
            if result.get('status')=='infrastructure_error' and result.get('reason')=='video_finalize_failed':
                report=Path(result['attempt'])/'result.json'
                errors=read_json(report).get('recording_errors',[]) if report.exists() else []
                if not any(e.get('failure_scope')=='shared' for e in errors):
                    result={**result,'original_status':'infrastructure_error','status':'recording_error',
                            'failure_scope':'episode','artifact_complete':False}
        except Exception as exc:
            caught=traceback.format_exc()
            result={**classify_episode_exception(exc,scope.phase),'station_id':station['station_id'],
                    'attempt':str(Path(root)/'attempts'/task['operation']/station['station_id']/attempt_id),
                    'executed_steps':getattr(scope.recorder,'steps',None),
                    'recorded_steps':getattr(scope.recorder,'steps',None),
                    'execution_started':any(getattr(r,'started',False) is True for r,_ in scope.resources),
                    'artifact_complete':False,
                    'task_completion':'unknown','video_status':'unavailable'}
        finally:
            cleanup_errors=scope.cleanup()
        if cleanup_errors:
            original=result
            result={**result,**next((e for e in cleanup_errors if e['failure_scope']=='shared'),cleanup_errors[0]),
                    'original_episode_status':original.get('status'),'cleanup_errors':cleanup_errors,
                    'artifact_complete':False}
        if result.get('status') in LOCAL_ERRORS:
            result={**result,'failure_scope':'episode','artifact_complete':False,'task_completion':'unknown'}
            if result.get('exception_detail'):
                result['attribution']=result['exception_detail']['attribution']
        if result.get('status')=='infrastructure_error':
            result['failure_scope']='shared'
        if result.get('status') in (*LOCAL_ERRORS,'infrastructure_error'):
            result['error_evidence']=episode_diagnostic(root,attempt_id,
                {'episode_result':result,'traceback':caught,'phase':scope.phase,
                 'attempt_exists':Path(result['attempt']).exists()})
        return result
    finally:
        _SCOPE.reset(token)


def isolated_episode(function):
    @wraps(function)
    def wrapper(*args,**kwargs):
        return execute_isolated_episode(function,*args,**kwargs)
    return wrapper



def recording_operation(function):
    @wraps(function)
    def wrapper(*args,**kwargs):
        scope=_SCOPE.get()
        previous=scope.phase if scope is not None else None
        if scope is not None: scope.phase='recording'
        # On error, retain recording phase for the enclosing isolation boundary.
        result=function(*args,**kwargs)
        if scope is not None: scope.phase=previous
        return result
    return wrapper

def isolated_recording(root, attempt_id, stage, function, *args, **kwargs):
    """Auxiliary/ledger write failure: persist diagnostic; shared storage errors stop."""
    diagnostics=kwargs.pop('_diagnostics',None)
    try:
        return function(*args,**kwargs)
    except Exception as exc:
        detail=classify_episode_exception(exc,'recording')
        record={'stage':stage,**detail,'traceback':traceback.format_exc()}
        if stage in ('trials','task_result') and len(args)>1 and args[1]:
            record['last_episode' if stage=='trials' else 'intended_task_result']=args[1][-1] if stage=='trials' else args[1]
        evidence=episode_diagnostic(root,attempt_id,record,stage)
        if diagnostics is not None:
            diagnostics.append({'stage':stage,**detail,'error_evidence':evidence})
        if detail['failure_scope']=='shared': raise
        return None
