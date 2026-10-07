"""Paired three-view RGB with a fixed union framing; all API angles adapted to degrees."""
import copy
from dataclasses import asdict, dataclass, replace
import itertools
import math
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

import imageio.v2 as imageio
import mujoco
import numpy as np

from ..io import digest, file_digest, write_json
from ..scenes.geometry import descendants, geom_points
from ..runtime.preparation import preparation_guard
from ..scenes.initialization import floor_support


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ViewConfig:
    width: int = 640
    height: int = 480
    fovy_rad: float = math.pi/4
    max_expansions: int = 3
    max_review_retries: int = 1
    initial_group_tolerance: float = .002
    camera_position_tolerance_m: float = .002
    camera_matrix_tolerance: float = .003
    initial_aux_views: int = 2
    max_aux_views: int = 3
    max_camera_trials: int = 32

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in (self.width, self.height)):
            raise ValueError('positive image dimensions required')
        if not math.isfinite(self.fovy_rad) or not .1 < self.fovy_rad < 2.5:
            raise ValueError('invalid fovy_rad')
        if any(type(v) is not int or not 0 <= v <= 5 for v in (self.max_expansions, self.max_review_retries)):
            raise ValueError('invalid view retry budget')
        if not (type(self.initial_aux_views) is int and type(self.max_aux_views) is int
                and 0 <= self.initial_aux_views <= self.max_aux_views <= 5
                and type(self.max_camera_trials) is int and 1 <= self.max_camera_trials <= 128):
            raise ValueError('invalid auxiliary view budget')
        for name in ('initial_group_tolerance', 'camera_position_tolerance_m', 'camera_matrix_tolerance'):
            value = getattr(self, name)
            if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
                raise ValueError('invalid pairing tolerance: ' + name)


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


class AuxiliaryViews:
    """Bounded, actual indoor camera selection. Never moves a robot or hides walls.

    A camera may cover a subset of requested regions; the selected set must cover
    their union. Before RGB is immutable and reused by proposals and reviews.
    """
    LIGHT_FIELDS = ('ambient', 'attenuation', 'bulbradius', 'castshadow', 'cutoff', 'diffuse',
                    'dir', 'exponent', 'headlight', 'id', 'intensity', 'pos', 'range', 'specular', 'texid', 'type')

    def __init__(self, sim, graph, context, observation, path, *, config=None, deadline=None):
        self.sim, self.graph, self.context, self.observation = sim, graph, context, observation
        self.path, self.config, self.deadline = Path(path), config or ViewConfig(), deadline
        self.path.mkdir(parents=True, exist_ok=True)
        self.views, self.requests = [], []
        self._renderers = {}
        self._publish()

    def _publish(self):
        self.observation['images'] = [self.observation['image'], *[
            {'view': 'before/' + v['name'], 'path': v['before_path'], 'not_robot_input': True,
             'sha256': v['sha256']} for v in self.views]]
        self.observation['auxiliary_views'] = [{k: v[k] for k in ('name', 'rig', 'coverage', 'sha256')} for v in self.views]
        write_json(self.path/'views.json', {'graph_id': self.graph.to_dict()['graph_id'],
                    'views': [{k: v[k] for k in ('name', 'rig', 'coverage', 'sha256', 'before_path', 'actual_position')} for v in self.views],
                    'requests': self.requests, 'not_robot_input': True})

    def _deadline(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError('auxiliary_camera_wall_clock_budget')

    @contextmanager
    def rendering(self, *sims):
        try:
            for sim in sims:
                if sim is not None and id(sim.model) not in self._renderers:
                    self._renderers[id(sim.model)] = mujoco.Renderer(sim.model, height=self.config.height, width=self.config.width)
            yield
        finally:
            for renderer in self._renderers.values():
                renderer.close()
            self._renderers = {}

    def _points(self, graph, nodes, regions, other_graph=None):
        result = {}
        for name in nodes:
            node = graph.nodes.get(name) or (other_graph.nodes.get(name) if other_graph else None)
            if node and node.get('pose'):
                box = node.get('collision_bounds_world')
                center = (np.asarray(box[0])+box[1])/2 if box else np.asarray(node['pose']['position'])
                result[name] = {'point': center, 'node': name, 'present': name in graph.nodes}
        work_regions = {r['region_id']: r for r in replace(self.context, graph=graph).work_regions()}
        for name in regions:
            if name not in work_regions:
                raise ValueError('unknown_auxiliary_work_region')
            result[name] = {'point': np.r_[work_regions[name]['center_xy_m'], .08], 'node': None, 'present': True}
        return result

    @staticmethod
    def _ray(sim, start, point):
        delta = np.asarray(point)-start
        distance = float(np.linalg.norm(delta))
        if distance < 1e-8:
            return False
        geom = np.array([-1], dtype=np.int32)
        hit = mujoco.mj_ray(sim.model, sim.data, start, delta/distance, None, True, -1, geom)
        # Empty work-region markers are not objects. Seeing the local floor or
        # an obstacle occupying that marker is useful layout evidence, not reachability.
        return bool(hit < 0 or hit >= distance-.15 or
                    np.linalg.norm((start+delta/distance*hit)[:2]-np.asarray(point)[:2]) < .25)

    def _coverage(self, sim, renderer, rois, *, segmentation=None):
        camera = renderer.scene.camera[0]
        pos = np.asarray(camera.pos, dtype=float)
        result = []
        for name, roi in rois.items():
            point = roi['point']
            if not in_frame(np.asarray([point]), camera, self.config.width, self.config.height):
                continue
            if not self._ray(sim, pos, point):
                continue
            if segmentation is not None and roi['node'] and roi['present']:
                try:
                    bodies = descendants(sim.model, sim.model.body(roi['node']).id)
                except KeyError:
                    continue
                ids = np.flatnonzero(np.isin(sim.model.geom_bodyid, list(bodies)))
                mask = (segmentation[:, :, 1] == int(mujoco.mjtObj.mjOBJ_GEOM)) & np.isin(segmentation[:, :, 0], ids)
                if int(mask.sum()) < 8:
                    continue
            result.append(name)
        return result

    def _frame(self, sim, rig, rois, *, lights=None, clearance=False, require_coverage=True):
        preparation_guard(sim)
        self._deadline()
        original = float(sim.model.vis.global_.fovy)
        renderer = self._renderers.get(id(sim.model))
        own = renderer is None
        try:
            sim.model.vis.global_.fovy = math.degrees(rig.fovy_rad)
            renderer = renderer or mujoco.Renderer(sim.model, height=rig.height, width=rig.width)
            renderer.disable_segmentation_rendering()
            renderer.update_scene(sim.data, camera=rig.camera(0))
            actual = renderer.scene.camera[0]
            pos = np.asarray(actual.pos, dtype=float)
            if clearance:
                if not floor_support(sim, pos[:2]) or pos[2] < .25:
                    return None
                for g in range(sim.model.ngeom):
                    local = (pos-sim.data.geom_xpos[g]) @ sim.data.geom_xmat[g].reshape(3, 3)
                    size, kind = sim.model.geom_size[g], sim.model.geom_type[g]
                    if kind == mujoco.mjtGeom.mjGEOM_BOX and np.all(np.abs(local) <= size+.03):
                        return None
                    if kind == mujoco.mjtGeom.mjGEOM_SPHERE and np.linalg.norm(local) <= size[0]+.03:
                        return None
                # Reject viewpoints in/very near solids using six actual geometry rays.
                for axis in np.eye(3):
                    for sign in (-1, 1):
                        g = np.array([-1], dtype=np.int32)
                        hit = mujoco.mj_ray(sim.model, sim.data, pos, axis*sign, None, True, -1, g)
                        if 0 <= hit < .06:
                            return None
            rough = self._coverage(sim, renderer, rois)
            if not rough and require_coverage:
                return None
            if lights is not None:
                renderer.scene.nlight = len(lights)
                for light, values in zip(renderer.scene.lights, lights):
                    for key, value in values.items():
                        setattr(light, key, value)
            snapshot = [{k: copy.deepcopy(getattr(light, k)) for k in self.LIGHT_FIELDS}
                        for light in renderer.scene.lights[:renderer.scene.nlight]]
            rgb = renderer.render().copy()
            renderer.enable_segmentation_rendering()
            segmentation = renderer.render().copy()
            coverage = self._coverage(sim, renderer, rois, segmentation=segmentation)
            return {'rgb': rgb, 'coverage': coverage, 'lights': snapshot,
                    'actual_position': pos.tolist(), 'actual_forward': np.asarray(actual.forward).tolist()}
        finally:
            if renderer is not None and own:
                renderer.close()
            sim.model.vis.global_.fovy = original

    def _rigs(self, points, hint):
        points = np.asarray(points)
        center = points.mean(axis=0)
        radius = max(float(np.linalg.norm(points-center, axis=1).max()), .35)
        elevations = {'top': [-1.35, -1.1], 'side': [-.3, -.55], 'overview': [-.7, -1.0],
                      'auto': [-.8, -1.2]}[hint]
        distance = max(.9, radius*1.6)
        for i in range(self.config.max_camera_trials):
            azimuth = (i % 12)*math.pi/6
            elevation = elevations[(i//12) % len(elevations)]
            scale = (1., 1.35)[(i//24) % 2]
            yield CameraRig(center.tolist(), distance*scale, self.config.fovy_rad,
                            self.config.width, self.config.height, ((azimuth, elevation),))

    def ensure(self, request, *, after_sim=None, after_graph=None, minimum_views=0):
        """Fulfil a structured aux_view request, without changing either state."""
        with self.rendering(self.sim, after_sim):
            return self._ensure(request, after_sim=after_sim, after_graph=after_graph, minimum_views=minimum_views)

    def _ensure(self, request, *, after_sim=None, after_graph=None, minimum_views=0):
        if hasattr(request, 'to_dict'):
            request = request.to_dict()
        nodes = request.get('node_ids') or [self.context.target]
        regions = request.get('work_region_ids', [])
        before_rois = self._points(self.graph, nodes, regions, after_graph)
        after_rois = self._points(after_graph, nodes, regions, self.graph) if after_graph else None
        if not before_rois:
            raise ValueError('no_renderable_information_request_ROI')
        wanted = set(before_rois)
        covered = set()
        for view in self.views:
            before = self._frame(self.sim, CameraRig(**view['rig']), before_rois)
            after = self._frame(after_sim, CameraRig(**view['rig']), after_rois, lights=view['lights']) if after_sim else None
            covered |= set(before['coverage'] if before else ()) & (set(after['coverage'] if after else ()) if after_sim else wanted)
        while (wanted-covered or len(self.views) < minimum_views) and len(self.views) < self.config.max_aux_views:
            best = None
            search = [roi['point'] for name, roi in before_rois.items() if name not in covered] or [r['point'] for r in before_rois.values()]
            if after_rois:
                search += [r['point'] for name, r in after_rois.items() if name not in covered]
            for rig in self._rigs(search, request.get('view_hint', 'auto')):
                if any(v['rig'] == rig.to_dict() for v in self.views):
                    continue
                before = self._frame(self.sim, rig, before_rois, clearance=True)
                if not before:
                    continue
                after = self._frame(after_sim, rig, after_rois, lights=before['lights'], clearance=True) if after_sim else None
                coverage = set(before['coverage']) & (set(after['coverage']) if after else set() if after_sim else wanted)
                if not coverage:
                    continue
                score = len(coverage-covered)
                if best is None or score > best[0]:
                    best = (score, rig, before, coverage)
                if score == len(wanted-covered) and (score > 0 or len(self.views) < minimum_views):
                    break
            if best is None or (best[0] == 0 and len(self.views) >= minimum_views):
                break
            _, rig, before, coverage = best
            name = f'aux_{len(self.views):03d}'
            image = self.path/('before_' + name + '.png')
            imageio.imwrite(image, before['rgb'])
            self.views.append({'name': name, 'rig': rig.to_dict(), 'before_path': str(image.resolve()),
                'sha256': file_digest(image), 'coverage': sorted(coverage), 'lights': before['lights'],
                'actual_position': before['actual_position']})
            covered |= coverage
        result = {'request': request, 'status': 'fulfilled' if wanted <= covered else 'information_insufficient',
                  'covered': sorted(covered), 'uncovered': sorted(wanted-covered), 'view_count': len(self.views)}
        self.requests.append(result)
        self._publish()
        return result

    def extend_pair(self, packet, after_sim, after_graph, path):
        """Append fixed auxiliary pairs and rebind every image to the new packet ID."""
        with self.rendering(after_sim):
            return self._extend_pair(packet, after_sim, after_graph, path)

    def _extend_pair(self, packet, after_sim, after_graph, path):
        packet = copy.deepcopy(packet)
        path = Path(path)
        packet['view_rigs'] = {}
        packet['auxiliary_coverage'] = {}
        packet['images'] = [i for i in packet['images'] if i['view'].endswith('/head')]
        for view in self.views:
            if file_digest(view['before_path']) != view['sha256']:
                raise ValueError('modified_before_auxiliary_image')
            # Work-region IDs are geometrical markers, not graph nodes.
            node_ids = [n for n in view['coverage'] if n in self.graph.nodes or n in after_graph.nodes]
            regions = [n for n in view['coverage'] if n not in node_ids]
            rois = self._points(after_graph, node_ids, regions, self.graph)
            frame = self._frame(after_sim, CameraRig(**view['rig']), rois, lights=view['lights'], require_coverage=False)
            for stage in ('before', 'after'):
                image = path/(stage + '_' + view['name'] + '.png')
                if stage == 'before':
                    shutil.copyfile(view['before_path'], image)
                else:
                    imageio.imwrite(image, frame['rgb'])
                packet['images'].append({'view': stage + '/' + view['name'], 'path': str(image.resolve()),
                                         'not_robot_input': True, 'sha256': file_digest(image)})
            packet['view_rigs'][view['name']] = view['rig']
            packet['auxiliary_coverage'][view['name']] = {'before': view['coverage'], 'after': frame['coverage']}
        packet['pair_id'] = digest({k: v for k, v in packet.items() if k not in ('pair_id', 'images')})
        for entry in packet['images']:
            entry['pair_id'] = packet['pair_id']
        write_json(path/'pair.json', packet)
        return packet
