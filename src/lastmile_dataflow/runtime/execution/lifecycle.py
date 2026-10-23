"""Full-lifecycle episode isolation; shared resource failures are fail-stop.

Failures remain results; cleanup never invents execution evidence.
"""

from contextvars import ContextVar
from functools import wraps
import errno
from pathlib import Path
import traceback


_SCOPE = ContextVar('episode_scope', default=None)
LOCAL_ERRORS = ('initialization_error', 'episode_error', 'recording_error')
_FATAL_ERRNOS = {
    errno.ENOSPC,
    errno.EDQUOT,
    errno.EROFS,
    errno.EACCES,
    errno.EPERM,
    errno.EIO,
    errno.ENODEV,
    errno.EMFILE,
    errno.ENFILE,
    errno.ENOMEM,
}


def classify_episode_exception(exc, phase='execution'):
    message = str(exc).lower()
    if getattr(exc, 'shared_resource', False):
        status, category = 'infrastructure_error', 'SharedPolicyFailure'
    elif (isinstance(exc, MemoryError) or type(exc).__name__ == 'OutOfMemoryError') or (
        isinstance(exc, OSError) and exc.errno in _FATAL_ERRNOS
    ):
        status, category = 'infrastructure_error', 'SharedResourceFailure'
    elif any(
        s in message
        for s in (
            'cuda out of memory',
            'cuda error',
            'device-side assert',
            'illegal memory access',
            'context lost',
            'egl_not_initialized',
            'valid opengl context has not been created',
            'egl_bad_alloc',
            'egl_bad_context',
            'cuda driver',
            'cuda initialization',
            'no cuda gpus are available',
        )
    ):
        status, category = 'infrastructure_error', 'SharedGPUFailure'
    else:
        if phase == 'initialize':
            status = 'initialization_error'
        elif phase in ('finalize', 'recording'):
            status = 'recording_error'
        else:
            status = 'episode_error'
        category = {
            'initialization_error': 'EpisodeInitializationError',
            'recording_error': 'EpisodeRecordingError',
            'episode_error': 'EpisodeExecutionError',
        }[status]
    return {
        'status': status,
        'failure_scope': 'shared' if status == 'infrastructure_error' else 'episode',
        'attribution': {
            'category': category,
            'error_type': type(exc).__name__,
            'message': str(exc),
            'phase': phase,
        },
        'reason': type(exc).__name__ + ':' + str(exc),
    }


def mark_episode_phase(phase):
    scope = _SCOPE.get()
    if scope is not None:
        scope.phase = phase


def track_resource(resource):
    scope = _SCOPE.get()
    if scope is not None:
        scope.resources.append(resource)
    return resource


def track_recorder(recorder):
    scope = _SCOPE.get()
    if scope is not None:
        scope.recorder = recorder
        scope.resources.append(recorder)


class EpisodeScope:
    def __init__(self):
        self.phase = 'initialize'
        self.resources = []
        self.recorder = None

    def cleanup(self, result):
        errors = []
        status = result['status'] if result is not None else 'incomplete'
        reason = result['reason'] if result is not None else 'interrupted'
        for resource in reversed(self.resources):
            try:
                if resource is self.recorder:
                    if not resource.finalizing:
                        resource.finish(status, reason)
                    else:
                        resource.abort_close()
                    errors.extend(resource.errors)
                else:
                    resource.close()
            except Exception as exc:
                errors.append({**classify_episode_exception(exc, 'finalize'),
                               'traceback': traceback.format_exc()})
        return errors


def execute_isolated_episode(function, *, root, station, attempt_id):
    scope = EpisodeScope()
    token = _SCOPE.set(scope)
    result = None
    try:
        try:
            result = function()
        except Exception as exc:
            classified = classify_episode_exception(exc, scope.phase)
            detail = {**classified, 'traceback': traceback.format_exc()}
            recorder = scope.recorder
            steps = recorder.steps if recorder is not None else None
            result = {
                **classified,
                'error': detail['traceback'],
                'exception_detail': detail,
                'execution_termination': {'status': classified['status'], 'reason': classified['reason'],
                                          'phase': scope.phase},
                'task_outcome': {'status': 'unknown', 'reason': classified['reason']},
                'station_id': station['station_id'],
                'attempt': str(Path(root) / 'rollouts' / attempt_id),
                'executed_steps': steps,
                'recorded_steps': steps,
                'execution_started': any(getattr(resource, 'started', False) is True for resource in scope.resources),
                'artifact_complete': False,
                'recording_complete': False,
                'task_completion': 'unknown',
                'video_status': 'unavailable',
            }
        finally:
            cleanup_errors = scope.cleanup(result)
        if scope.recorder is not None:
            recorder = scope.recorder
            result.update(rollout_id=recorder.attempt_id,
                          seed=recorder.config.get('seed'),
                          recording_complete=recorder.finished and recorder.result['recording_complete'])
        if cleanup_errors:
            original_status = result['status']
            failure = next((error for error in cleanup_errors if error['failure_scope'] == 'shared'), cleanup_errors[0])
            result.update(failure)
            result.update(original_episode_status=original_status, cleanup_errors=cleanup_errors,
                          artifact_complete=False, recording_complete=False)
        if result['status'] in LOCAL_ERRORS:
            result.update(failure_scope='episode', artifact_complete=False, task_completion='unknown')
        elif result['status'] == 'infrastructure_error':
            result['failure_scope'] = 'shared'
        return result
    finally:
        _SCOPE.reset(token)


def isolated_episode(function):
    @wraps(function)
    def wrapper(baseline, task, station, *args, root, attempt_id, **kwargs):
        return execute_isolated_episode(
            lambda: function(baseline, task, station, *args, root=root, attempt_id=attempt_id, **kwargs),
            root=root,
            station=station,
            attempt_id=attempt_id,
        )

    return wrapper


def recording_operation(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        scope = _SCOPE.get()
        previous = scope.phase if scope is not None else None
        if scope is not None:
            scope.phase = 'recording'
        # On error, retain recording phase for the enclosing isolation boundary.
        result = function(*args, **kwargs)
        if scope is not None:
            scope.phase = previous
        return result

    return wrapper
