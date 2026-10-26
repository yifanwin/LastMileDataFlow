"""Place measured assets on floor or task support surfaces."""
import numpy as np
from .regions import rotated_placement, ground_coverage
from ..geometry import support_rays
from ..stations.initialization import floor_height


def sample_placement(sim, geometry, point, yaw, config, *, region=None):
    if region is None:
        # Ground plane is measured by a ray; never assume a floor at z=0.
        xy = np.asarray(point['xy'])
        height = floor_height(sim, xy)
        if height is None:
            return None, 'no_ground_support'
        pose, points = rotated_placement(geometry['local_points'], xy, yaw, height, config.placement_gap_m)
        if not ground_coverage(sim, points):
            return None, 'full_footprint_outside_ground'
        coverage = False
        kind = 'ground'
    else:
        xy = np.asarray(point['xy'])
        pose, points = rotated_placement(geometry['local_points'], xy, yaw, region.height, config.placement_gap_m)
        local = region.local(points)
        a, b, c, d = region.bounds
        if not (local[:, 0].min() >= a + .01 and local[:, 0].max() <= b - .01
                and local[:, 1].min() >= c + .01 and local[:, 1].max() <= d - .01):
            return None, 'full_footprint_outside_surface'
        if not support_rays(sim, 0, region, points):
            return None, 'surface_support_rays_failed'
        height, coverage, kind = region.height, True, 'surface'
    return {'asset_id': geometry['asset_id'], 'category': geometry['category'],
            'point_id': point['point_id'], 'point_xy': xy.tolist(), 'point_sources': point['sources'],
            'pose': pose, 'yaw_rad': float(yaw), 'kind': kind, 'support_height_m': float(height),
            'support_geom': region.geom if region is not None else None,
            'support_instance': region.support if region is not None else None,
            'surface_coverage_verified': coverage, 'measured_size_m': geometry['size'],
            'world_bounds': {'min': points.min(axis=0).tolist(), 'max': points.max(axis=0).tolist()}}, None
