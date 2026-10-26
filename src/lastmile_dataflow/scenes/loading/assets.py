"""Rigid asset adaptation and bounded, shared runtime collision geometry."""
from collections import OrderedDict
import copy
from pathlib import Path
from types import SimpleNamespace
import math
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from ...utils.io import read_json
from ..geometry import body_points, descendants, extract_regions
from .mjcf import load_spec


THOR_ADAPTER_VERSION = 'thor-rigid-z-up-v1'


def rigid_thor_spec(source, asset_id):
    """Adapt a read-only THOR y-up source to one free, rigid z-up root."""
    source = Path(source).absolute()
    raw = load_spec(source)
    roots = list(raw.worldbody.bodies)
    if len(roots) != 1 or list(raw.worldbody.geoms) or list(raw.lights) or list(raw.actuators) or list(raw.sensors) or list(raw.equalities):
        raise ValueError('one_rigid_asset_without_world_controls_required')
    for joint in raw.joints:
        if joint.parent != roots[0] or joint.type != mujoco.mjtJoint.mjJNT_FREE:
            raise ValueError('articulated_asset_unsupported')
    tree = ET.fromstring(raw.to_xml())
    compiler = tree.find('compiler')
    compiler.set('fusestatic', 'true')
    world = tree.find('worldbody')
    asset_root = world.find('body')
    for joint in list(asset_root):
        if joint.tag in ('joint', 'freejoint'):
            asset_root.remove(joint)
    world.remove(asset_root)
    name = asset_id + '_construction_root'
    body = ET.SubElement(world, 'body', name=name)
    ET.SubElement(body, 'freejoint', name=name + '_free')
    frame = ET.SubElement(body, 'frame', quat=f'{math.sqrt(.5)} {math.sqrt(.5)} 0 0')
    frame.append(asset_root)
    return mujoco.MjSpec.from_string(ET.tostring(tree, encoding='unicode')), name


def measure_asset(spec, entry):
    """Compile once; retain model/data and local points for placement and edits."""
    body = spec.body(entry['root_body'])
    if body is None or body.parent != spec.worldbody or len(list(spec.worldbody.bodies)) != 1:
        raise ValueError('asset requires one top-level root body')
    if list(spec.worldbody.geoms) or list(spec.lights) or list(spec.actuators) or list(spec.sensors) or list(spec.equalities):
        raise ValueError('asset world geometry/lights/controls/constraints unsupported')
    # MuJoCo compilation fuses static children/frames in the spec. Keep the
    # original adapter for later export and scene attachment.
    model = spec.copy().compile()
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    root = model.body(entry['root_body']).id
    count, adr = int(model.body_jntnum[root]), int(model.body_jntadr[root])
    if count and not (count == 1 and model.jnt_type[adr] == mujoco.mjtJoint.mjJNT_FREE):
        raise ValueError('articulated asset root unsupported')
    if any(model.body_jntnum[b] for b in descendants(model, root) - {root}):
        raise ValueError('articulated asset descendants unsupported')
    sim = SimpleNamespace(model=model, data=data)
    points = body_points(sim, root)
    local = (points - data.xpos[root]) @ data.xmat[root].reshape(3, 3)
    lo, hi = local.min(axis=0), local.max(axis=0)
    if not np.isfinite(points).all() or np.any(hi - lo <= 0):
        raise ValueError('asset qualified collision geometry unavailable')
    record = {**copy.deepcopy(entry), 'root_motion': 'free' if count else 'fixed',
              'collision_bounds_local': [lo.tolist(), hi.tolist()],
              'size': dict(zip(('width', 'depth', 'height'), (hi - lo).tolist())),
              'pose': {'frame': 'world', 'position': data.xpos[root].tolist(),
                       'quaternion_wxyz': data.xquat[root].tolist()},
              'regions': [r.to_dict() for r in extract_regions(sim, entry['root_body'])]}
    return {'spec': spec, 'record': record, 'model': model, 'data': data,
            'body': root, 'local_points': local}


class RuntimeGeometryCache:
    """Task-local LRU of isolated assets, separate from session topology models."""
    def __init__(self, assets, *, base_dir='.', capacity=16):
        if capacity < 1:
            raise ValueError('positive geometry cache capacity required')
        self.base_dir, self.capacity = Path(base_dir), capacity
        self.assets, self._cache = {}, OrderedDict()
        self.compile_count = 0
        for entry in assets:
            if entry['asset_id'] in self.assets:
                raise ValueError('duplicate asset identity')
            self.assets[entry['asset_id']] = copy.deepcopy(entry)

    def _get(self, asset_id):
        if asset_id not in self._cache:
            entry = self.assets[asset_id]
            spec = load_spec(self.base_dir / entry['xml_path'])
            geometry = measure_asset(spec, entry)
            self.compile_count += 1
            if len(self._cache) >= self.capacity:
                self._cache.popitem(last=False)
            self._cache[asset_id] = geometry
        self._cache.move_to_end(asset_id)
        return self._cache[asset_id]

    def load(self, asset_id):
        geometry = self._get(asset_id)
        return geometry['spec'].copy(), copy.deepcopy(geometry['record'])

    def geometry(self, asset_id):
        geometry = self._get(asset_id)
        return {**copy.deepcopy(geometry['record']), **{key: geometry[key]
                for key in ('local_points', 'model', 'data', 'body')}}

    def close(self):
        self._cache.clear()


class EditAssetCatalog(RuntimeGeometryCache):
    """Explicit catalog input used by construction sessions and edit tools."""
    def __init__(self, path=None, *, assets=None):
        self.path = Path(path).absolute() if path else None
        value = {'asset_catalog_version': '0.1', 'assets': assets} if assets is not None else read_json(self.path) if path else {'asset_catalog_version': '0.1', 'assets': []}
        if value['asset_catalog_version'] != '0.1':
            raise ValueError('invalid edit asset catalog')
        super().__init__(value['assets'], base_dir=self.path.parent if self.path else '.')

    def select(self, selector, rng):
        candidates = []
        for asset_id in sorted(self.assets):
            if selector.get('asset_id', asset_id) != asset_id:
                continue
            entry = self.assets[asset_id]
            if 'category' in selector and entry.get('category') not in selector['category']:
                continue
            if 'type' in selector and entry.get('type') != selector['type']:
                continue
            try:
                _, asset = self.load(asset_id)
            except (ValueError, OSError):
                continue
            if any(not lo <= asset['size'][name] <= hi for name, (lo, hi) in selector.get('size', {}).items()):
                continue
            candidates.append(asset)
        if not candidates:
            raise ValueError('no_loadable_asset_matches_selector')
        return candidates[int(rng.integers(len(candidates)))]

    def describe(self):
        result = []
        for asset_id in sorted(self.assets):
            try:
                _, asset = self.load(asset_id)
                result.append({k: asset[k] for k in ('asset_id', 'category', 'type', 'root_motion', 'size')})
            except (ValueError, OSError) as exc:
                result.append({'asset_id': asset_id, 'status': 'unsupported', 'reason': type(exc).__name__})
        return result
