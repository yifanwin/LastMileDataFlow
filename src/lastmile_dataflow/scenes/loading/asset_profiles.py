"""Offline THOR profiles: one measured JSONL index, read-only source assets."""
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
import math
import time

from ...utils.io import append_row, read_json, rows
from .assets import THOR_ADAPTER_VERSION, measure_asset, rigid_thor_spec
from .mjcf import save_spec


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
