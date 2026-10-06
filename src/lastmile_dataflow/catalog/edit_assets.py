"""Operation-capability asset catalog, without legacy verified/approval labels."""
import copy
from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np

from ..io import read_json
from ..scenes.geometry import body_points, descendants, extract_regions


class EditAssetCatalog:
    def __init__(self, path=None):
        self.path = Path(path).resolve() if path else None
        value = read_json(self.path) if path else {'asset_catalog_version': '0.1', 'assets': []}
        if set(value) != {'asset_catalog_version', 'assets'} or value['asset_catalog_version'] != '0.1' or not isinstance(value['assets'], list):
            raise ValueError('invalid edit asset catalog')
        self.assets, self._cache = {}, {}
        for asset in value['assets']:
            if not isinstance(asset, dict) or not {'asset_id', 'xml_path', 'root_body'} <= set(asset) <= {'asset_id', 'xml_path', 'root_body', 'category', 'type'}:
                raise ValueError('invalid asset record')
            if not all(isinstance(v, str) and v for v in asset.values()) or asset['asset_id'] in self.assets:
                raise ValueError('invalid or duplicate asset identity')
            self.assets[asset['asset_id']] = copy.deepcopy(asset)

    def load(self, asset_id):
        if asset_id not in self.assets:
            raise ValueError('asset_missing:' + str(asset_id))
        entry = self.assets[asset_id]
        path = (self.path.parent / entry['xml_path']).resolve()
        if not path.is_file():
            raise ValueError('asset_file_unreadable')
        if asset_id not in self._cache:
            spec = mujoco.MjSpec.from_file(str(path))
            body = spec.body(entry['root_body'])
            if body is None or body.parent != spec.worldbody or len(list(spec.worldbody.bodies)) != 1:
                raise ValueError('asset requires one top-level root body')
            if list(spec.worldbody.geoms) or list(spec.lights) or list(spec.actuators) or list(spec.sensors) or list(spec.equalities):
                raise ValueError('asset world geometry/lights/controls/constraints unsupported')
            model = spec.compile()
            data = mujoco.MjData(model)
            mujoco.mj_forward(model, data)
            root = model.body(entry['root_body']).id
            count, adr = int(model.body_jntnum[root]), int(model.body_jntadr[root])
            if count and not (count == 1 and model.jnt_type[adr] == mujoco.mjtJoint.mjJNT_FREE):
                raise ValueError('articulated asset root unsupported')
            if any(model.body_jntnum[b] for b in descendants(model, root)-{root}):
                raise ValueError('articulated asset descendants unsupported')
            points = body_points(SimpleNamespace(model=model, data=data), root)
            local = (points-data.xpos[root]) @ data.xmat[root].reshape(3, 3)
            lo, hi = local.min(axis=0), local.max(axis=0)
            geometry = {'asset_id': asset_id, 'category': entry.get('category'), 'type': entry.get('type'),
                        'root_motion': 'free' if count else 'fixed',
                        'collision_bounds_local': [lo.tolist(), hi.tolist()],
                        'size': dict(zip(('width', 'depth', 'height'), (hi-lo).tolist())),
                        'pose': {'frame': 'world', 'position': data.xpos[root].tolist(),
                                 'quaternion_wxyz': data.xquat[root].tolist()},
                        'regions': [r.to_dict() for r in extract_regions(SimpleNamespace(model=model, data=data), entry['root_body'])]}
            if not np.isfinite(points).all() or np.any(hi-lo <= 0):
                raise ValueError('asset qualified collision geometry unavailable')
            self._cache[asset_id] = (spec, geometry)
        spec, geometry = self._cache[asset_id]
        return spec.copy(), {**copy.deepcopy(entry), **copy.deepcopy(geometry)}

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
