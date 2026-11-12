"""Shared corpus loading and pose FPS."""
import numpy as np


def load_grasps(path, body, max_rows=1024):
    with np.load(path, allow_pickle=False) as source:
        poses = source["transforms"]
    if poses.size == 0:
        return []
    if poses.ndim != 3 or poses.shape[1:] != (4, 4):
        raise ValueError("invalid grasp shape")
    result = []
    for row in np.linspace(0, len(poses) - 1, min(max_rows, len(poses)), dtype=int):
        pose = poses[row].astype(float)
        if not np.isfinite(pose).all() or not np.allclose(pose[3], [0, 0, 0, 1], atol=1e-5):
            continue
        u, singular, v = np.linalg.svd(pose[:3, :3])
        if np.max(np.abs(singular - 1)) > 0.02 or np.linalg.det(pose[:3, :3]) <= 0:
            continue
        pose[:3, :3] = u @ v
        result.append(dict(pose_local=pose, body=body, source=str(path), row=int(row)))
    return result


def select_grasps(candidates, poses, home, count=16, position_scale_m=0.1, rotation_scale_rad=np.pi / 2):
    if position_scale_m <= 0 or rotation_scale_rad <= 0:
        raise ValueError("FPS scales must be positive")
    if not candidates:
        return []
    poses = np.asarray(poses)
    nearest = np.argsort(np.linalg.norm(poses[:, :3, 3] - home, axis=1), kind="stable")[:64]
    poses = poses[nearest]
    delta = poses[:, None, :3, 3] - poses[None, :, :3, 3]
    cosine = (np.einsum("aij,bij->ab", poses[:, :3, :3], poses[:, :3, :3]) - 1) / 2
    distances = (delta ** 2).sum(-1) / position_scale_m ** 2 + (np.arccos(np.clip(cosine, -1, 1)) / rotation_scale_rad) ** 2
    selected = [0]
    minimum = distances[0].copy()
    while len(selected) < min(count, len(nearest)):
        minimum[selected] = -1
        tied = np.flatnonzero(minimum == minimum.max())
        index = int(tied[np.argmin(nearest[tied])])
        selected.append(index)
        minimum = np.minimum(minimum, distances[index])
    return [candidates[nearest[i]] for i in selected]
