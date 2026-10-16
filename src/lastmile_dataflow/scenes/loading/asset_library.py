"""Bounded THOR category retrieval and derived rigid adapters, no approval whitelist."""
from pathlib import Path
import math
import os
import time
import xml.etree.ElementTree as ET
import mujoco
import numpy as np

from .assets import EditAssetCatalog
from ...utils.io import write_json, read_json

from .mjcf import load_spec


class ThorLibrary:
    """Explicit THOR y-up convention; never applies this transform to Objaverse."""
    def __init__(self, root=None):
        self.root = Path(root).resolve() if root else None
        self.directories = {}
        if self.root:
            if not self.root.is_dir():
                raise FileNotFoundError(self.root)
            for group in sorted(self.root.iterdir()):
                if not group.is_dir():
                    continue
                for category in sorted(group.iterdir()):
                    if (category/'Prefabs').is_dir():
                        self.directories.setdefault(category.name, []).append(category/'Prefabs')

    def categories(self):
        return sorted(self.directories)

    def retrieve(self, requests, path, *, limit=3, seed=42, deadline=None, registered_only=False):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=False)
        entries, records, seen = [], [], set()
        rng = np.random.default_rng(seed)
        types_path = self.root / 'asset_id_to_object_type.json' if self.root else None
        asset_types = read_json(types_path) if types_path and types_path.is_file() else {}
        if registered_only and not asset_types:
            raise ValueError('registered_asset_map_required')
        for request in requests:
            for category in request['categories']:
                candidates = []
                for directory in self.directories.get(category, []):
                    # Known THOR names avoid thousands of remote stat calls.
                    # Only sampled models are opened below; unreadable inputs are skipped there.
                    if registered_only:
                        names = sorted(name for name, kind in asset_types.items() if kind == category)
                    else:
                        with os.scandir(directory) as directory_entries:
                            names = sorted(entry.name for entry in directory_entries if entry.is_dir())
                    candidates.extend(directory / name / (name + '.xml') for name in names)
                accepted = 0
                for index in rng.permutation(len(candidates))[:max(limit*4, limit)]:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError('asset_retrieval_wall_clock_budget')
                    source = candidates[int(index)]
                    asset_id = source.stem
                    if asset_id in seen:
                        continue
                    row = {'asset_id': asset_id, 'category': category, 'source_xml': str(source), 'status': 'loading',
                           'thor_registered': asset_types.get(asset_id) == category}
                    records.append(row)
                    derived = path/asset_id
                    derived.mkdir(exist_ok=False)
                    try:
                        raw = load_spec(source)
                        roots = list(raw.worldbody.bodies)
                        if len(roots) != 1 or list(raw.worldbody.geoms) or list(raw.lights) or list(raw.actuators) or list(raw.sensors) or list(raw.equalities):
                            raise ValueError('one_rigid_asset_without_world_controls_required')
                        root = roots[0]
                        for joint in list(raw.joints):
                            if joint.parent != root or joint.type != mujoco.mjtJoint.mjJNT_FREE:
                                raise ValueError('articulated_asset_unsupported')
                        raw.compiler.meshdir = str(source.parent)
                        raw.compiler.texturedir = str(source.parent)
                        # Derive XML, not a live-spec reparent/delete: keep native
                        # joint ownership intact and preserve resolved asset paths.
                        tree = ET.fromstring(raw.to_xml())
                        compiler = tree.find('compiler')
                        compiler.set('fusestatic', 'true')
                        compiler.set('meshdir', str(source.parent))
                        compiler.set('texturedir', str(source.parent))
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
                        model = mujoco.MjModel.from_xml_string(ET.tostring(tree, encoding='unicode'))
                        xml = derived/'asset.xml'
                        mujoco.mj_saveLastXML(str(xml), model)
                        entry = {'asset_id': asset_id, 'xml_path': str(xml.relative_to(path)), 'root_body': name,
                                 'category': category, 'type': 'object'}
                        manifest = derived/'manifest.json'
                        catalog = EditAssetCatalog(manifest, assets=[{**entry, 'xml_path': str(xml.resolve())}])
                        _, geometry = catalog.load(asset_id)
                        if any(not bounds[0] <= geometry['size'][key] <= bounds[1]
                               for key, bounds in request['size_constraints_m'].items()):
                            raise ValueError('measured_dimensions_outside_request')
                        entries.append(entry)
                        seen.add(asset_id)
                        row.update(status='loadable', size=geometry['size'],
                                   adapter='explicit_THOR_y_up_to_z_up_free_root_rigid_fusion', derived_xml=str(xml.resolve()))
                        accepted += 1
                    except (ValueError, OSError) as exc:
                        row.update(status='unsupported', reason=type(exc).__name__ + ':' + str(exc)[:500])
                    if accepted >= limit:
                        break
        manifest = path/'catalog.json'
        write_json(manifest, {'asset_catalog_version': '0.1', 'assets': entries})
        return EditAssetCatalog(manifest)
