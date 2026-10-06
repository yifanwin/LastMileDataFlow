"""Paired three-view RGB with a fixed union framing; all API angles adapted to degrees."""
import copy
from dataclasses import asdict, dataclass
import itertools
import math
from pathlib import Path

import imageio.v2 as imageio
import mujoco
import numpy as np

from ..io import digest, write_json
from ..scenes.geometry import descendants, geom_points
from ..runtime.preparation import preparation_guard


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ViewConfig:
    width: int = 640
    height: int = 480
    fovy_rad: float = math.pi/4
    max_expansions: int = 3
    max_review_retries: int = 1

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in (self.width, self.height)):
            raise ValueError('positive image dimensions required')
        if not math.isfinite(self.fovy_rad) or not .1 < self.fovy_rad < 2.5:
            raise ValueError('invalid fovy_rad')
        if any(type(v) is not int or not 0 <= v <= 5 for v in (self.max_expansions, self.max_review_retries)):
            raise ValueError('invalid view retry budget')


@dataclass(frozen=True)
class CameraRig:
    lookat: list
    distance_m: float
    fovy_rad: float
    width: int
    height: int
    # Radians internally; only the MuJoCo free-camera adapter converts to degrees.
    angles: tuple = ((0., -math.pi/2+.01), (math.pi/4, -.65), (-3*math.pi/4, -.65))

    @classmethod
    def fit(cls, points, config=None, *, expansion=1.):
        config = config or ViewConfig()
        points = np.asarray(points)
        if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
            raise ValueError('finite union framing points required')
        lo, hi = points.min(axis=0), points.max(axis=0)
        center = (lo+hi)/2
        radius = max(float(np.linalg.norm(hi-lo)/2), .15)
        half_angle = min(config.fovy_rad/2, math.atan(math.tan(config.fovy_rad/2)*config.width/config.height))
        distance = radius/math.sin(half_angle)*1.25*expansion
        return cls(center.tolist(), distance, config.fovy_rad, config.width, config.height)

    def camera(self, index):
        cam = mujoco.MjvCamera()
        cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        cam.lookat = self.lookat
        cam.distance = self.distance_m
        cam.azimuth, cam.elevation = [math.degrees(a) for a in self.angles[index]]
        return cam

    def to_dict(self):
        return asdict(self)


def framing_points(sim, graph, names):
    points = []
    for name in names:
        node = graph.nodes.get(name)
        if node is None:
            continue
        if node.get('mjcf_body'):
            body = sim.model.body(node['mjcf_body']).id
            bodies = descendants(sim.model, body)
            for g in range(sim.model.ngeom):
                if int(sim.model.geom_bodyid[g]) not in bodies:
                    continue
                try:
                    points.extend(geom_points(sim, g))  # includes visual, not just collision geoms
                except ValueError:
                    radius = float(sim.model.geom_rbound[g])
                    if radius > 0:
                        points.extend(np.array(list(itertools.product((-radius, radius), repeat=3))) + sim.data.geom_xpos[g])
        elif node.get('pose'):
            points.append(node['pose']['position'])
            if node.get('kind') == 'station':
                # Cover the actual robot context, not just a mathematical base point.
                for g in range(sim.model.ngeom):
                    if not sim.model.body(int(sim.model.geom_bodyid[g])).name.startswith(sim.robot.config.namespace):
                        continue
                    try:
                        points.extend(geom_points(sim, g))
                    except ValueError:
                        radius = float(sim.model.geom_rbound[g])
                        if radius > 0:
                            points.extend(np.array(list(itertools.product((-radius, radius), repeat=3))) + sim.data.geom_xpos[g])
    return np.asarray(points)


def in_frame(points, camera, width, height):
    """Project union vertices through actual rendered GL camera, not guessed azimuth."""
    points = np.asarray(points)
    forward, up = np.asarray(camera.forward), np.asarray(camera.up)
    right = np.cross(forward, up)
    delta = points-np.asarray(camera.pos)
    depth = delta @ forward
    if np.any(depth <= max(camera.frustum_near, 1e-8)) or np.any(depth >= camera.frustum_far):
        return False
    x, y = (delta @ right)/depth, (delta @ up)/depth
    near = camera.frustum_near
    low_y, high_y = camera.frustum_bottom/near, camera.frustum_top/near
    half_x = (high_y-low_y)/2 * width/height
    center_x = camera.frustum_center/near
    # Keep 5% clearance to the viewport boundaries.
    return bool(np.all(np.abs(x-center_x) <= half_x*.95) and
                np.all(y >= low_y*.95) and np.all(y <= high_y*.95))


def render_edit_pair(before_sim, after_sim, before_graph, after_graph, affected, path, *, sample_id,
                     config=None, context_nodes=(), expansion=1., view_rotation_rad=0.):
    preparation_guard(before_sim)
    preparation_guard(after_sim)
    config = config or ViewConfig()
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    names = set(affected) | set(context_nodes)
    before_points = framing_points(before_sim, before_graph, names)
    after_points = framing_points(after_sim, after_graph, names)
    arrays = [p for p in (before_points, after_points) if len(p)]
    if not arrays:
        raise RenderError('no_actual_geometry_for_framing')
    union = np.concatenate(arrays)
    local_names = set(affected) | {n for n in context_nodes
        if before_graph.nodes.get(n, {}).get('root_motion') == 'free'}
    local_points = [framing_points(s, g, local_names) for s, g in
                    ((before_sim, before_graph), (after_sim, after_graph))]
    local_arrays = [p for p in local_points if len(p)]
    local_union = np.concatenate(local_arrays) if local_arrays else union
    renderers, originals = [], {}
    try:
        for sim in (before_sim, after_sim):
            originals.setdefault(id(sim.model), (sim.model, float(sim.model.vis.global_.fovy)))
            sim.model.vis.global_.fovy = math.degrees(config.fovy_rad)
            renderers.append(mujoco.Renderer(sim.model, height=config.height, width=config.width))
        for attempt in range(config.max_expansions+1):
            rig = CameraRig.fit(union, config, expansion=expansion*1.4**attempt)
            local_rig = CameraRig.fit(local_union, config, expansion=expansion*1.4**attempt)
            def rotated(r):
                return CameraRig(r.lookat, r.distance_m, r.fovy_rad, r.width, r.height,
                                 tuple((az+view_rotation_rad, el) for az, el in r.angles))
            rigs = [rotated(rig), rotated(local_rig), rotated(local_rig)]
            frames, covered = [], True
            light_snapshots = []
            for stage, sim, points, renderer in zip(('before', 'after'), (before_sim, after_sim),
                                                   (before_points, after_points), renderers):
                for i, view in enumerate(('top', 'oblique_a', 'oblique_b')):
                    view_rig = rigs[i]
                    renderer.update_scene(sim.data, camera=view_rig.camera(i))
                    # Identical clip planes and actual baseline lights at each view.
                    for camera in renderer.scene.camera:
                        factor = .005/camera.frustum_near
                        camera.frustum_bottom *= factor
                        camera.frustum_top *= factor
                        camera.frustum_center *= factor
                        camera.frustum_width *= factor
                        camera.frustum_near = .005
                        camera.frustum_far = max(100., rig.distance_m*4)
                        actual_fovy = math.atan(camera.frustum_top/camera.frustum_near)-math.atan(camera.frustum_bottom/camera.frustum_near)
                        if not math.isclose(actual_fovy, config.fovy_rad, abs_tol=1e-6):
                            raise RenderError('rendered_fovy_does_not_match_config')
                    if stage == 'before':
                        light_snapshots.append([{k: copy.deepcopy(getattr(light, k)) for k in
                            ('ambient', 'attenuation', 'bulbradius', 'castshadow', 'cutoff', 'diffuse',
                             'dir', 'exponent', 'headlight', 'id', 'intensity', 'pos', 'range',
                             'specular', 'texid', 'type')} for light in renderer.scene.lights[:renderer.scene.nlight]])
                    else:
                        renderer.scene.nlight = len(light_snapshots[i])
                        for light, values in zip(renderer.scene.lights, light_snapshots[i]):
                            for key, value in values.items():
                                setattr(light, key, value)
                    view_points = points if i == 0 else local_points[0 if stage == 'before' else 1]
                    covered &= bool(not len(view_points) or in_frame(view_points, renderer.scene.camera[0], config.width, config.height))
                    rgb = renderer.render().copy()
                    if rgb.shape != (config.height, config.width, 3):
                        raise RenderError('invalid_render_shape')
                    frames.append((stage + '/' + view, rgb))
            if covered:
                break
        if not covered:
            raise RenderError('objects_still_out_of_frame')
        packet = {'sample_id': sample_id, 'before_graph_id': before_graph.to_dict()['graph_id'],
                  'after_graph_id': after_graph.to_dict()['graph_id'], 'rig': rigs[0].to_dict(),
                  'view_rigs': {v: r.to_dict() for v, r in zip(('top', 'oblique_a', 'oblique_b'), rigs)},
                  'framing': 'top_with_context_oblique_affected_union_all_paired',
                  'lighting': 'same_source_model_lighting_no_asset_lights', 'expansions': attempt,
                  'not_robot_input': True, 'images': []}
        packet['pair_id'] = digest(packet)
        for view, rgb in frames:
            file = path / (view.replace('/', '_') + '.png')
            imageio.imwrite(file, rgb)
            packet['images'].append({'view': view, 'path': str(file.resolve()), 'pair_id': packet['pair_id']})
        write_json(path/'pair.json', packet)
        return packet
    except Exception as exc:
        write_json(path/'render_error.json', {'status': 'infrastructure_error', 'reason': type(exc).__name__})
        if isinstance(exc, RenderError):
            raise
        raise RenderError('real_render_failed:' + type(exc).__name__) from None
    finally:
        for renderer in renderers:
            renderer.close()
        for model, fovy in originals.values():
            model.vis.global_.fovy = fovy
