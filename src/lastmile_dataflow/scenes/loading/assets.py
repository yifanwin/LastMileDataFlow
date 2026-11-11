"""Rigid asset adaptation and bounded, shared runtime collision geometry."""
from collections import OrderedDict, Counter
from dataclasses import asdict, dataclass, field
import time
import copy
from pathlib import Path
from types import SimpleNamespace
import math
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from ...utils.io import append_row, read_json, rows
from ..geometry import body_points, descendants, extract_regions, rotated_extent
from .mjcf import load_spec, save_spec


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



ROLE_CATEGORIES = {
    'floor_obstacle': {'Chair', 'OfficeChair', 'Stool', 'Footstool', 'Ottoman',
                       'GarbageCan', 'HousePlant', 'FloorLamp', 'DogBed'},
    'visual_occluder': {'Vase', 'CoffeeMachine', 'Kettle', 'Toaster', 'DeskLamp', 'HousePlant'},
    'arm_obstacle': {'CoffeeMachine', 'Kettle', 'Toaster', 'DeskLamp', 'Vase', 'Bowl', 'Pan'},
    'approach_obstacle': {'CoffeeMachine', 'Kettle', 'Vase', 'DeskLamp', 'Bowl', 'Pan'},
    'small_clutter': {'Cup', 'Mug', 'Bottle', 'Bowl', 'Plate', 'Book', 'SaltShaker', 'PepperShaker'},
    'support_furniture': {'DiningTable', 'Desk', 'CounterTop', 'CoffeeTable', 'SideTable'},
}
SURFACE_SUPPORTS = ('Table', 'DiningTable', 'Desk', 'CounterTop', 'CoffeeTable', 'SideTable', 'Shelf')
CATEGORY_NAMES = {''.join(c for c in name.lower() if c.isalnum()): name
                  for categories in ROLE_CATEGORIES.values() for name in categories}
CATEGORY_NAMES.update({'coffeemaker': 'CoffeeMachine',
                       'plant': 'HousePlant', 'saltshaker': 'SaltShaker', 'peppershaker': 'PepperShaker'})


def canonical_category(value):
    key = ''.join(c for c in value.lower() if c.isalnum())
    return CATEGORY_NAMES.get(key, value)


@dataclass(frozen=True)
class AssetProfile:
    asset_id: str
    source_xml: str
    category: str
    width_m: float | None = None
    depth_m: float | None = None
    height_m: float | None = None
    footprint_m2: float | None = None
    movable: bool = False
    loadable: bool = False
    support_kind: str = 'unknown'
    compatible_supports: tuple = ()
    compatible_rooms: tuple = ('*',)
    roles: tuple = ()
    xml_path: str | None = None
    root_body: str | None = None
    adapter_version: str = THOR_ADAPTER_VERSION
    reason: str | None = None

    def geometry_entry(self):
        return {'asset_id': self.asset_id, 'category': self.category, 'type': 'object',
                'xml_path': self.xml_path, 'root_body': self.root_body,
                'adapter_version': self.adapter_version}


def iter_thor_assets(root, *, categories=(), max_per_category=None):
    """Traverse nested RoboTHOR Prefabs as well as ordinary THOR categories."""
    root = Path(root)
    registered = root / 'asset_id_to_object_type.json'
    types = read_json(registered) if registered.is_file() else {}
    requested = {canonical_category(c) for c in categories}
    seen, counts = set(), Counter()
    for prefabs in sorted(root.rglob('Prefabs')):
        for asset in sorted(prefabs.iterdir()):
            if not asset.is_dir():
                continue
            identity = asset.name
            category = canonical_category(types.get(identity, prefabs.parent.name))
            if identity in seen or requested and category not in requested:
                continue
            if max_per_category is not None and counts[category] >= max_per_category:
                continue
            seen.add(identity)
            counts[category] += 1
            yield identity, category, asset / (identity + '.xml')


def build_asset_index(source_root, output, *, categories=(), max_per_category=None):
    """Measure rigid adapters once offline; unsupported entries remain explicit."""
    source_root, output = Path(source_root).absolute(), Path(output).absolute()
    if not source_root.is_dir():
        raise FileNotFoundError(source_root)
    output.mkdir(parents=True, exist_ok=False)
    index = output / 'assets.jsonl'
    index.touch()
    start = time.monotonic()
    count = Counter()
    for identity, category, source in iter_thor_assets(
            source_root, categories=categories, max_per_category=max_per_category):
        roles = tuple(role for role, allowed in ROLE_CATEGORIES.items() if category in allowed)
        floor = 'floor_obstacle' in roles or 'support_furniture' in roles
        surface = any(role in roles for role in ('visual_occluder', 'arm_obstacle', 'approach_obstacle', 'small_clutter'))
        supports = (('floor',) if floor else ()) + (SURFACE_SUPPORTS if surface else ())
        common = {'asset_id': identity, 'source_xml': str(source), 'category': category,
                  'roles': roles, 'movable': bool(roles and 'support_furniture' not in roles),
                  'support_kind': 'floor_or_surface' if floor and surface else 'floor' if floor else 'surface' if surface else 'unknown',
                  'compatible_supports': supports}
        try:
            spec, body = rigid_thor_spec(source, identity)
            entry = {'asset_id': identity, 'root_body': body, 'category': category, 'type': 'object'}
            geometry = measure_asset(spec, entry)
            size = geometry['record']['size']
            folder = output / 'geometry' / identity
            folder.mkdir(parents=True)
            xml = folder / 'asset.xml'
            save_spec(spec, xml)
            profile = AssetProfile(**common, loadable=True, width_m=size['width'],
                depth_m=size['depth'], height_m=size['height'],
                footprint_m2=size['width'] * size['depth'], xml_path=str(xml), root_body=body)
            count['loadable'] += 1
        except (ValueError, OSError) as exc:
            profile = AssetProfile(**common, reason=f'{type(exc).__name__}:{str(exc)[:400]}')
            count['unsupported'] += 1
        append_row(index, asdict(profile))
    return {'index': str(index), 'source_root': str(source_root), 'counts': dict(count),
            'elapsed_s': time.monotonic() - start, 'adapter_version': THOR_ADAPTER_VERSION}


def load_asset_profiles(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    profiles = []
    seen = set()
    for row in rows(path):
        profile = AssetProfile(**row)
        if profile.asset_id in seen or profile.adapter_version != THOR_ADAPTER_VERSION:
            raise ValueError('duplicate asset or unsupported profile adapter version')
        if profile.loadable and (not profile.xml_path or not profile.root_body or any(
                value is None or not math.isfinite(value) or value <= 0
                for value in (profile.width_m, profile.depth_m, profile.height_m, profile.footprint_m2))):
            raise ValueError('loadable profiles require an adapter reference and positive measured dimensions')
        seen.add(profile.asset_id)
        profiles.append(profile)
    return profiles


PLACEMENT_MARGIN_M = .01


@dataclass(frozen=True)
class AssetRequest:
    role: str
    support: str | None = None
    excluded_categories: tuple = ()
    available_size_m: tuple | None = None
    limit: int = 5


class AssetSelector:
    """Select lightweight profiles only; no source scan or geometry compile."""
    def __init__(self, index):
        self.profiles = load_asset_profiles(index)
        self.category_uses, self.asset_uses = Counter(), Counter()

    def select(self, request, rng):
        excluded = {canonical_category(c) for c in request.excluded_categories}
        candidates = []
        for profile in self.profiles:
            if not profile.loadable or not profile.movable or profile.category in excluded or request.role not in profile.roles:
                continue
            if request.support and request.support not in profile.compatible_supports:
                continue
            if request.available_size_m is not None:
                available = np.asarray(request.available_size_m) - 2 * PLACEMENT_MARGIN_M
                fits = False
                for yaw in (0., np.pi / 2):
                    extent = rotated_extent(profile.width_m, profile.depth_m, yaw)
                    fits |= bool(np.all(np.asarray(extent) <= available))
                if not fits:
                    continue
            candidates.append(profile)
        # Prefer unused categories within each shortlist and previously used
        # less often in the task. Random ties remain reproducible by worker seed.
        selected, shortlist_counts = [], Counter()
        while candidates and len(selected) < min(5, request.limit):
            scores = [(self.category_uses[p.category] + shortlist_counts[p.category],
                       self.asset_uses[p.asset_id], rng.random()) for p in candidates]
            index = min(range(len(scores)), key=scores.__getitem__)
            profile = candidates.pop(index)
            selected.append(profile)
            shortlist_counts[profile.category] += 1
        return selected

    def record_use(self, profile):
        self.category_uses[profile.category] += 1
        self.asset_uses[profile.asset_id] += 1
