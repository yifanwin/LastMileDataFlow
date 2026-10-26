"""Task support surfaces and complete footprint placement geometry."""
import itertools
import numpy as np
from ..geometry import extract_regions
from ..stations.initialization import floor_support, inside_triangles, room_triangles


def support_regions(sim, task):
    """Select the plane beneath the target, not exposed tops of table legs."""
    candidates = []
    for support in task['support_obstacles']:
        candidates.extend(extract_regions(sim, support['body']))
    anchor = np.asarray(task['anchor_world'])
    beneath = []
    for region in candidates:
        local = region.local(anchor.reshape(1, 3))[0]
        a, b, c, d = region.bounds
        if a <= local[0] <= b and c <= local[1] <= d and region.height <= anchor[2] + .001:
            beneath.append(region)
    if not beneath:
        return []
    actual = max(beneath, key=lambda r: r.height)
    # Coplanar patches of the same actual support are allowed; lower shelves are not.
    return [r for r in candidates if r.support == actual.support and abs(r.height - actual.height) <= .001]


def rotated_placement(local_points, xy, yaw, height, gap):
    """Align footprint CENTER, not a possibly off-center asset root, to the point."""
    c, s = np.cos(yaw), np.sin(yaw)
    rotation = np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]])
    rotated = np.asarray(local_points) @ rotation.T
    center = (rotated[:, :2].min(axis=0) + rotated[:, :2].max(axis=0)) / 2
    position = np.r_[np.asarray(xy) - center, height + gap - rotated[:, 2].min()]
    return {'position': position.tolist(), 'quaternion_wxyz': [float(np.cos(yaw / 2)), 0., 0., float(np.sin(yaw / 2))]}, rotated + position


def footprint_samples(points):
    lo, hi = np.asarray(points)[:, :2].min(axis=0), np.asarray(points)[:, :2].max(axis=0)
    return np.array(list(itertools.product(np.linspace(lo[0], hi[0], 5),
                                          np.linspace(lo[1], hi[1], 5))))


def ground_coverage(sim, points):
    triangles = room_triangles(sim)
    samples = footprint_samples(points)
    return all(inside_triangles(xy, triangles) and floor_support(sim, xy) for xy in samples)
