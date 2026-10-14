"""Deterministic NumPy implementation of the MolmoBot head-image augmentation.

Geometry matches olmo/data/image_warping_utils.py: 4:3 centre crop, a 120-degree
augmentation K, radial inverse sampling, bilinear zero padding, 30% edge crop.
This is a model augmentation, NOT a calibration conversion between real lenses.
"""
import math

import numpy as np

COEFFICIENTS = {'k1': .051, 'k2': .144, 'k3': .015, 'k4': -.018}


def fisheye(image, *, seed):
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError('expected uint8 RGB image')
    h, original_w = image.shape[:2]
    w = min(original_w, h * 4 // 3)
    left = (original_w - w) // 2
    cropped = image[:, left:left + w]
    rng = np.random.RandomState(seed)
    coefficients = {k: float(v + rng.uniform(-.001, .001)) for k, v in COEFFICIENTS.items()}
    focal = np.float32(.5 * h / math.tan(math.radians(60)))
    y, x = np.indices((h, w), dtype=np.float32)
    nx, ny = (x - w / 2) / focal, (y - h / 2) / focal
    r = np.sqrt(nx ** 2 + ny ** 2)
    factor = np.ones_like(r)
    for index, value in enumerate(coefficients.values(), 1):
        factor += np.float32(value) * r ** (2 * index)
    sx, sy = nx * factor * focal + w / 2, ny * factor * focal + h / 2
    x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
    dx, dy = sx - x0, sy - y0
    output = np.zeros((h, w, 3), dtype=np.float32)
    for xx, yy, weight in ((x0, y0, (1 - dx) * (1 - dy)), (x0 + 1, y0, dx * (1 - dy)),
                           (x0, y0 + 1, (1 - dx) * dy), (x0 + 1, y0 + 1, dx * dy)):
        valid = (xx >= 0) & (xx < w) & (yy >= 0) & (yy < h)
        output[valid] += cropped[yy[valid], xx[valid]].astype(np.float32) * weight[valid, None]
    crop_y, crop_x = int(h * .30), int(w * .30)
    ys = slice(crop_y, h - crop_y)
    xs = slice(crop_x, w - crop_x)
    valid = (sx >= 0) & (sx <= w - 1) & (sy >= 0) & (sy <= h - 1)
    metadata = {'kind': 'derived', 'transform': 'molmobot_head_radial_augmentation_v1',
                'seed': seed, 'distortion_parameters': coefficients, 'randomization_factor': .001,
                'source_resolution': [original_w, h], 'centre_crop_left_px': left,
                'intermediate_resolution': [w, h], 'augmentation_fovy_deg': 120.,
                'augmentation_K': [[float(focal), 0, w / 2], [0, float(focal), h / 2], [0, 0, 1]],
                'edge_crop_fraction': .30, 'edge_crop_pixels': [crop_x, crop_y],
                'interpolation': 'bilinear', 'padding': 'zeros', 'pixel_origin': 'top_left',
                'not_physical_lens_calibration': True}
    return np.clip(output[ys, xs], 0, 255).astype(np.uint8), metadata, {
        'source_x_px': sx[ys, xs] + left, 'source_y_px': sy[ys, xs], 'valid_mask': valid[ys, xs]}


def transform_point(point, metadata):
    """Use the SAME realized coefficients as the saved conditioning image."""
    point = np.asarray(point, float)
    if point.shape != (2,) or not np.isfinite(point).all() or np.any(point < 0) or np.any(point > 1):
        raise ValueError('conditioning point must be normalized source-image [x,y]')
    original_w, h = metadata['source_resolution']
    w = metadata['intermediate_resolution'][0]
    focal = metadata['augmentation_K'][0][0]
    inputs = (point * [original_w, h] - [metadata['centre_crop_left_px'] + w / 2, h / 2]) / focal
    output = inputs.copy()
    for _ in range(20):
        radius = np.linalg.norm(output)
        factor = 1 + sum(value * radius ** (2 * i) for i, value in enumerate(metadata['distortion_parameters'].values(), 1))
        output = inputs / factor
    crop_x, crop_y = metadata['edge_crop_pixels']
    final = (output * focal + [w / 2 - crop_x, h / 2 - crop_y]) / [w - 2 * crop_x, h - 2 * crop_y]
    if not np.isfinite(final).all() or np.any(final < 0) or np.any(final > 1):
        raise ValueError('conditioning point outside fisheye crop')
    return final
