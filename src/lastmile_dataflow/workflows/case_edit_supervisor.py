"""Killable CLI worker: wall deadline includes native load/compile/render and HTTP."""
from dataclasses import asdict
import multiprocessing
from pathlib import Path
import time
import uuid

from ..config import CollectionConfig, RobotConfig
from ..construction.case_schema import CaseEditRequest, RunResult
from ..io import read_json, write_json
from ..recording.edit_views import ViewConfig
from ..runtime.preparation import SettleConfig
from .case_edit import run_case_edit, validate_run_id


def _case_edit_worker(request, robot, collection, options):
    run_case_edit(CaseEditRequest.from_dict(request), RobotConfig(**robot), CollectionConfig(**collection),
                  settle_config=SettleConfig(**options.pop('settle_config')),
                  view_config=ViewConfig(**options.pop('view_config')), **options)


def supervised_case_edit(request, robot, collection, *, api_settings, run_id=None, asset_catalog=None,
                         settle_config=None, view_config=None, initial_snapshot=None, provider=None, _worker=None):
    request = request if isinstance(request, CaseEditRequest) else CaseEditRequest.from_dict(request)
    run_id = validate_run_id(run_id or 'case-edit-' + uuid.uuid4().hex[:12])
    path = Path(collection.output_dir).resolve()/'case_edits'/run_id
    if path.exists():
        raise FileExistsError(path)
    options = {'provider': provider, 'api_settings': str(api_settings) if api_settings else None, 'run_id': run_id,
               'asset_catalog': str(asset_catalog) if asset_catalog else None,
               'settle_config': asdict(settle_config or SettleConfig()),
               'view_config': asdict(view_config or ViewConfig()),
               'initial_snapshot': str(initial_snapshot) if initial_snapshot else None}
    process = multiprocessing.get_context('spawn').Process(target=_worker or _case_edit_worker,
        args=(request.to_dict(), asdict(robot), asdict(collection), options))
    start = time.monotonic()
    interrupted = False
    process.start()
    try:
        process.join(request.generation.timeout_s)
    except KeyboardInterrupt:
        interrupted = True
    terminated = process.is_alive()
    if terminated:
        process.terminate()
        process.join(2.)
        if process.is_alive():
            process.kill()
            process.join(2.)
    if not terminated and not interrupted and process.exitcode == 0 and (path/'result.json').is_file():
        if read_json(path/'result.json')['status'] != 'partial':
            return path
    path.mkdir(parents=True, exist_ok=True)
    if not (path/'request.json').exists():
        write_json(path/'request.json', request.to_dict())
    if (path/'result.json').is_file():
        result = read_json(path/'result.json')
        if result['status'] == 'completed':
            return path  # Completed artifacts are not changed by slow cleanup.
    else:
        result = RunResult(run_id, 'partial', request.generation.target_scene_count).to_dict()
    result.update(status='interrupted' if interrupted else 'budget_exhausted' if terminated else 'infrastructure_error',
                  reason='user_interrupt' if interrupted else 'hard_wall_clock_deadline' if terminated else f'worker_exit_without_result:{process.exitcode}')
    write_json(path/'result.json', RunResult.from_dict(result).to_dict())
    write_json(path/'supervisor.json', {'wall_time_s': time.monotonic()-start, 'terminated': terminated,
                                      'exitcode': process.exitcode, 'interrupted': interrupted})
    return path
