"""Hard deadline for native scene compilation, rendering and HTTP, not just Python loops."""
from dataclasses import asdict
import multiprocessing
from pathlib import Path
import time
import uuid

from ..config import CollectionConfig, RobotConfig
from ..construction.scene_request import SceneConstructionRequest
from ..io import read_json, write_json
from ..recording.edit_views import ViewConfig
from ..runtime.preparation import SettleConfig
from .case_edit import validate_run_id, run_case_edit, initial_result


def _case_edit_worker(request, robot, collection, options):
    run_case_edit(SceneConstructionRequest.from_dict(request), RobotConfig(**robot), CollectionConfig(**collection),
        settle_config=SettleConfig(**options.pop('settle_config')),
        view_config=ViewConfig(**options.pop('view_config')), **options)


def supervised_case_edit(request, robot, collection, *, api_settings, provider=None, run_id=None,
                                settle_config=None, view_config=None, model=None, _worker=None):
    request = request if isinstance(request, SceneConstructionRequest) else SceneConstructionRequest.from_dict(request)
    run_id = validate_run_id(run_id or 'construct-' + uuid.uuid4().hex[:12])
    path = Path(collection.output_dir).resolve()/'case_construction'/run_id
    if path.exists():
        raise FileExistsError(path)
    options = {'api_settings': str(api_settings) if api_settings else None, 'provider': provider, 'model': model, 'run_id': run_id,
        'settle_config': asdict(settle_config or SettleConfig()), 'view_config': asdict(view_config or ViewConfig())}
    process = multiprocessing.get_context('spawn').Process(target=_worker or _case_edit_worker,
        args=(request.to_dict(), asdict(robot), asdict(collection), options))
    start = time.monotonic()
    interrupted = False
    process.start()
    try:
        process.join(request.budgets.timeout_s)
    except KeyboardInterrupt:
        interrupted = True
    terminated = process.is_alive()
    if terminated:
        process.terminate()
        process.join(2.)
        if process.is_alive():
            process.kill()
            process.join(2.)
    if (path/'result.json').is_file():
        result = read_json(path/'result.json')
        if result['status'] == 'completed' or (not interrupted and not terminated and process.exitcode == 0 and result['status'] != 'running'):
            return path
    else:
        result = initial_result(request, run_id)
    path.mkdir(parents=True, exist_ok=True)
    if not (path/'request.json').exists():
        write_json(path/'request.json', request.to_dict())
    result.update(status='interrupted' if interrupted else 'budget_exhausted' if terminated else 'infrastructure_error',
        reason='user_interrupt' if interrupted else 'hard_wall_clock_deadline' if terminated else f'worker_exit_without_final_result:{process.exitcode}')
    write_json(path/'result.json', result)
    write_json(path/'supervisor.json', {'terminated': terminated, 'interrupted': interrupted,
               'exitcode': process.exitcode, 'wall_time_s': time.monotonic()-start})
    return path
