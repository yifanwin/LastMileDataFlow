"""Deterministic bearing/range coverage; never prefix-truncate valid stations."""
import numpy as np


def coverage_select(stations, anchor, limit=20):
    valid = [s for s in stations if s['geometry'] == 'valid']
    if type(limit) is not int or limit <= 0:
        raise ValueError('positive station limit required')
    if len({s['station_id'] for s in valid}) != len(valid):
        raise ValueError('duplicate station identity')
    if not valid:
        return [], {'method': 'bearing_range_stratified_farthest_point', 'input_count': 0, 'final_count': 0}
    delta = np.array([s['xy'] for s in valid]) - np.asarray(anchor[:2])
    radius = np.linalg.norm(delta, axis=1)
    theta = np.arctan2(delta[:, 1], delta[:, 0])
    rnorm = radius / max(float(radius.max()), 1e-9)
    features = np.column_stack((delta / max(float(radius.max()), 1e-9),
                                np.cos(theta), np.sin(theta), 2 * rnorm))
    # Seed with both range extremes, then one representative per angular/radial bin.
    selected = []
    def add(index):
        index = int(index)
        if index not in selected and len(selected) < limit:
            selected.append(index)
    add(np.argmin(radius)); add(np.argmax(radius))
    sector = np.minimum(7, ((theta + np.pi) / (2 * np.pi) * 8).astype(int))
    radial = np.minimum(1, (rnorm * 2).astype(int))
    for band in range(2):
        for angle in range(8):
            ids = np.flatnonzero((sector == angle) & (radial == band))
            if len(ids):
                center = features[ids].mean(axis=0)
                add(ids[np.argmin(np.linalg.norm(features[ids] - center, axis=1))])
    while len(selected) < min(limit, len(valid)):
        distance = np.min(np.linalg.norm(features[:, None, :] - features[selected][None, :, :], axis=2), axis=1)
        distance[selected] = -1
        add(np.argmax(distance))
    # Do not shuffle small sets unnecessarily; still no [:limit] selection.
    kept = valid if len(valid) <= limit else [valid[i] for i in selected]
    ids = {s['station_id'] for s in kept}
    discarded = [{'station_id': s['station_id'], 'reason': 'coverage_compression'} for s in valid if s['station_id'] not in ids]
    return kept, {'method': 'bearing_range_stratified_farthest_point', 'input_count': len(valid),
                  'final_count': len(kept), 'discarded': discarded,
                  'selected_ids': [s['station_id'] for s in kept],
                  'range_m': [float(radius.min()), float(radius.max())],
                  'angular_bins': 8, 'radial_bins': 2,
                  'features': 'normalized_xy, cos_bearing, sin_bearing, 2*normalized_range'}
