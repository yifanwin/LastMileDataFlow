"""Bounded THOR category retrieval and derived rigid adapters, no approval whitelist."""
from pathlib import Path
import math
import time
import xml.etree.ElementTree as ET
import mujoco
import numpy as np

from .edit_assets import EditAssetCatalog
from ..io import file_digest, write_json
from ..scenes.mjcf import load_spec


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

    def retrieve(self, requests, path, *, limit=3, seed=42, deadline=None):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=False)
        entries, records, seen = [], [], set()
        rng = np.random.default_rng(seed)
        for request in requests:
            for category in request['categories']:
                candidates = []
                for directory in self.directories.get(category, []):
                    # Select the canonical asset XML, not *_mesh/prim/old variants.
                    for asset in sorted(directory.iterdir()):
                        if asset.is_dir() and (asset/(asset.name + '.xml')).is_file():
                            candidates.append(asset/(asset.name + '.xml'))
                accepted = 0
                for index in rng.permutation(len(candidates))[:max(limit*4, limit)]:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError('asset_retrieval_wall_clock_budget')
                    source = candidates[int(index)]
                    asset_id = source.stem
                    if asset_id in seen:
                        continue
                    row = {'asset_id': asset_id, 'category': category, 'source_xml': str(source), 'status': 'loading'}
                    records.append(row)
                    derived = path/asset_id
                    derived.mkdir(exist_ok=False)
                    try:
                        row['source_sha256'] = file_digest(source)
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
                        entry = {'asset_id': asset_id, 'xml_path': str(xml.resolve()), 'root_body': name,
                                 'category': category, 'type': 'object'}
                        manifest = derived/'manifest.json'
                        write_json(manifest, {'asset_catalog_version': '0.1', 'assets': [entry]})
                        catalog = EditAssetCatalog(manifest)
                        _, geometry = catalog.load(asset_id)
                        if any(not bounds[0] <= geometry['size'][key] <= bounds[1]
                               for key, bounds in request['size_constraints_m'].items()):
                            raise ValueError('measured_dimensions_outside_request')
                        if row['source_sha256'] != file_digest(source):
                            raise ValueError('source_asset_changed')
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
        write_json(path/'retrieval.json', {'library': str(self.root) if self.root else None,
                   'requests': requests, 'seed': seed, 'records': records, 'loadable_count': len(entries)})
        return EditAssetCatalog(manifest)
