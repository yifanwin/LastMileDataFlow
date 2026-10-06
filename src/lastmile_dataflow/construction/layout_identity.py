"""Deduplicate final layouts, not operations, generated names, or timestamps."""
import json
import numpy as np
from ..io import digest


def layout_identity(graph, baseline_names, *, position_tolerance_m=.001, quaternion_tolerance=.001):
    nodes = graph.nodes if hasattr(graph, 'nodes') else graph['nodes']
    entries = []
    for name, node in nodes.items():
        if node.get('kind') != 'object' or not node.get('pose'):
            continue
        pose = node['pose']
        q = np.asarray(pose['quaternion_wxyz'], dtype=float)
        first = next((v for v in q if abs(v) > 1e-9), 1.)
        if first < 0:
            q = -q
        identity = 'asset:' + str(node['asset_id']) if node.get('asset_id') else 'original:' + name
        if not node.get('asset_id') and name not in baseline_names:
            raise ValueError('added objects require asset identity for deduplication')
        item = {'identity': identity,
                'asset_id': node.get('asset_id'),
                'position_bins': np.rint(np.asarray(pose['position'])/position_tolerance_m).astype(int).tolist(),
                'quaternion_bins': np.rint(q/quaternion_tolerance).astype(int).tolist(),
                'shape_local': node.get('collision_bounds_local')}
        # Float round-off in local collision geometry is not a different asset.
        if item['shape_local'] is not None:
            shape = np.round(item['shape_local'], 6)
            shape[np.abs(shape) < .5e-6] = 0.
            item['shape_local'] = shape.tolist()
        entries.append(item)
    entries.sort(key=lambda item: json.dumps(item, sort_keys=True))
    return digest(entries)
