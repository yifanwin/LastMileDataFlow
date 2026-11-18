"""Shared corpus loading and pose FPS."""
import numpy as np


DEFAULT_FPS_COUNT = 16
DEFAULT_FPS_SEED = 42


def load_grasps(path, body, max_rows=None):
    with np.load(path, allow_pickle=False) as source:
        poses = source["transforms"]
    if poses.size == 0:
        return []
    if poses.ndim != 3 or poses.shape[1:] != (4, 4):
        raise ValueError("invalid grasp shape")
    result = []
    rows = range(len(poses)) if max_rows is None else np.linspace(0, len(poses) - 1, min(max_rows, len(poses)), dtype=int)
    for row in rows:
        pose = poses[row].astype(float)
        if not np.isfinite(pose).all() or not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-5):
            continue
        u, singular, v = np.linalg.svd(pose[:3, :3])
        if np.max(np.abs(singular - 1)) > 0.02 or np.linalg.det(pose[:3, :3]) <= 0:
            continue
        pose[:3, :3] = u @ v
        result.append(dict(pose_local=pose, body=body, source=str(path), row=int(row)))
    return result


def select_grasps(candidates, poses, count=DEFAULT_FPS_COUNT, position_scale_m=0.1,
                  rotation_scale_rad=np.pi / 2, *, seed=DEFAULT_FPS_SEED):
    """Pose FPS over every candidate; a seeded first point makes the pool reproducible."""
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError("FPS count must be a positive integer")
    if position_scale_m <= 0 or rotation_scale_rad <= 0:
        raise ValueError("FPS scales must be positive")
    if not candidates:
        return []
    poses = np.asarray(poses)
    positions, rotations = poses[:, :3, 3], poses[:, :3, :3]
    index = int(np.random.default_rng(seed).integers(len(candidates)))
    selected = []
    minimum = np.full(len(candidates), np.inf)
    for _ in range(min(count, len(candidates))):
        selected.append(index)
        delta = positions - positions[index]
        cosine = (np.einsum("aij,ij->a", rotations, rotations[index]) - 1) / 2
        distances = (delta ** 2).sum(-1) / position_scale_m ** 2
        distances += (np.arccos(np.clip(cosine, -1, 1)) / rotation_scale_rad) ** 2
        minimum = np.minimum(minimum, distances)
        minimum[selected] = -1
        # Corpus order preserves source-row order; argmax resolves ties stably.
        index = int(np.argmax(minimum))
    return [candidates[i] for i in selected]
