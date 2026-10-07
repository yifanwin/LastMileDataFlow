"""Bounded pre-begin robot placement with real target visibility; not navigation."""
import copy
import math
import time
from pathlib import Path
import mujoco
import numpy as np
import imageio.v2 as imageio

from ..io import write_json
from ..recording.head_views import head_frame, save_observation
from ..recording.edit_views import ViewConfig
from ..scenes.initialization import floor_support
from ..validation.lightweight import inspect_state
from .preparation import PreparedScene, SettleConfig, preparation_guard, settle_scene
from .simulation import Simulation
from .build_session import migrate_state


class VisibleInitializationUnavailable(ValueError):
    pass


def projection_visible(sim, target):
    camera = sim.model.camera(sim.robot.camera_names['head_camera']).id
    point = sim.data.xpos[sim.model.body(target).id]
    local = sim.data.cam_xmat[camera].reshape(3, 3).T @ (point-sim.data.cam_xpos[camera])
    if -local[2] <= .01:
        return False
    return abs(local[1]/local[2]) < math.tan(math.radians(float(sim.model.cam_fovy[camera]))/2)*.95


def prepare_visible(prepared, context, config, collection, path, *, seed, deadline, settle_config=None, view_config=None):
    """The same baseline is reused for all samples in this context/round."""
    baseline = prepared.sim
    preparation_guard(baseline)
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(seed)
    target = np.asarray(context.graph.nodes[context.target]['pose']['position'])
    trials = []
    view_config = view_config or ViewConfig()
    renderer = mujoco.Renderer(baseline.model, height=view_config.height, width=view_config.width)
    sim = None
    transferred = False
    best = None
    valid_count = 0
    try:
        for index in range(config.max_initialization_trials):
            if time.monotonic() >= deadline:
                if best is not None:
                    break
                raise TimeoutError('visible_initialization_wall_clock_budget')
            if sim is not None:
                sim.close()
            # Every placement starts from the same unedited source state; failed
            # settling may not contaminate the next initialization trial.
            sim = Simulation(baseline.model, baseline.robot.config, copy.deepcopy(baseline.catalog))
            sim.source, sim.restoration, sim.spec = baseline.source, baseline.restoration, baseline.spec
            if hasattr(baseline, 'frozen_provenance'):
                sim.frozen_provenance = baseline.frozen_provenance
            migrate_state(baseline, sim)
            # Stratify azimuth and vary distance/head. This samples poses, not a base route.
            azimuth = (index % 16)*2*math.pi/16 + rng.uniform(-.08, .08)
            distance = float(rng.uniform(*config.observation_distance_m))
            xy = target[:2] + distance*np.array([math.cos(azimuth), math.sin(azimuth)])
            yaw = math.atan2(target[1]-xy[1], target[0]-xy[0])
            initial = copy.deepcopy(sim.robot.config.initial)
            initial['base'] = [*xy.tolist(), yaw]
            pitch = (.6, .25, -.1, .9)[(index//16) % 4]
            initial['head'] = [0., pitch]
            row = {'index': index, 'base': initial['base'], 'head': initial['head'], 'valid': False}
            trials.append(row)
            try:
                sim.robot.initialize(initial)
                # Use the ACTUAL mounted camera forward direction and offset,
                # not an assumed camera/robot x-axis convention.
                for _ in range(3):
                    cid = sim.model.camera(sim.robot.camera_names['head_camera']).id
                    forward = -sim.data.cam_xmat[cid].reshape(3, 3)[:, 2]
                    delta = target[:2]-sim.data.cam_xpos[cid, :2]
                    error = math.atan2(delta[1], delta[0])-math.atan2(forward[1], forward[0])
                    initial['base'][2] = (initial['base'][2]+error+math.pi) % (2*math.pi)-math.pi
                    sim.robot.initialize(initial)
                row['base'] = list(initial['base'])
            except ValueError as exc:
                row['reason'] = 'joint_limits:' + str(exc)
                continue
            row['floor_support'] = floor_support(sim, xy)
            check = inspect_state(sim, collection)
            row['physical_errors'] = [i for i in check['issues'] if i['severity'] == 'error']
            if not row['floor_support'] or not check['valid']:
                row['reason'] = 'floor_or_collision'
                continue
            if not projection_visible(sim, context.target):
                row['reason'] = 'target_outside_vertical_frustum'
                continue
            _, _, visibility = head_frame(sim, context.target, view_config, renderer=renderer)
            row['visibility'] = visibility
            if visibility['visible_pixels'] < config.min_target_pixels:
                row['reason'] = 'target_not_visible'
                continue
            settling_config = settle_config or SettleConfig()
            settling = settle_scene(sim, settling_config, deadline=deadline,
                                    require_stability=settling_config.require_source_stability)
            row['settling_valid'] = settling['valid']
            if not settling['valid']:
                row['reason'] = 'settling_failed'
                continue
            rgb, _, final_visibility = head_frame(sim, context.target, view_config, renderer=renderer)
            if final_visibility['visible_pixels'] < config.min_target_pixels:
                row['reason'] = 'target_not_visible_after_settling'
                continue
            row['valid'] = True
            row['final_visibility'] = final_visibility
            preview = path/'candidates'/f'initial_{index:03d}.png'
            preview.parent.mkdir(parents=True, exist_ok=True)
            imageio.imwrite(preview, rgb)
            row['head_rgb'] = str(preview.resolve())
            row['initial'] = copy.deepcopy(initial)
            valid_count += 1
            if best is None or final_visibility['visible_pixels'] > best[3]['visible_pixels']:
                if best is not None:
                    best[0].close()
                best = (sim, copy.deepcopy(initial), settling, final_visibility, index)
                sim = None
            if valid_count >= config.initialization_candidates:
                break
        if best is not None:
            selected, initial, settling, _, index = best
            initialization = {'status': 'valid', 'method': 'bounded_target_visible_initialization',
                'seed': seed, 'selected_initial': initial, 'selected_index': index,
                'requested_candidates': config.initialization_candidates, 'valid_candidates': valid_count,
                'selection': 'largest_actual_target_visibility', 'navigation_reachability': 'unknown', 'trials': trials}
            result = PreparedScene(selected, initialization, settling, graph_config=settling_config.graph_config())
            result.save(path/'baseline')
            observation = save_observation(selected, result.graph, context.target, path/'observation', config=view_config)
            write_json(path/'initialization.json', initialization)
            transferred = True
            return result, observation
        write_json(path/'initialization.json', {'status': 'not_found_within_budget', 'seed': seed, 'trials': trials})
        raise VisibleInitializationUnavailable('no_legal_target_visible_initial_state_within_budget')
    finally:
        renderer.close()
        if sim is not None and not transferred:
            sim.close()
        elif sim is not None and (best is None or sim is not best[0]):
            sim.close()
        if best is not None and not transferred:
            best[0].close()
