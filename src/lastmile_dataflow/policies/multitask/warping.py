from __future__ import annotations

import math
from typing import List, Optional
import numpy as np
import torch
import torch.nn.functional as F

GOPRO_CAMERA_HEIGHT = 480
GOPRO_CAMERA_WIDTH = 640
GOPRO_VERTICAL_FOV = 120
DEFAULT_CROP_PERCENT = 0.3
CAMERAS_TO_WARP: List[str] = ["head_camera"]
DEFAULT_DISTORTION_PARAMETERS = {"k1": 0.051, "k2": 0.144, "k3": 0.015, "k4": -0.018}


def calc_camera_intrinsics(
    fov_y: float, frame_height: int, frame_width: int
) -> np.ndarray:
    """Compute camera intrinsic matrix from vertical FOV and frame dimensions."""
    focal_length = 0.5 * frame_height / math.tan(math.radians(fov_y / 2))
    f_x = f_y = focal_length
    c_x = frame_width / 2
    c_y = frame_height / 2
    K = np.array([[f_x, 0, c_x], [0, f_y, c_y], [0, 0, 1]])
    return K


def make_distorted_grid(
    H: int,
    W: int,
    K: torch.Tensor,
    distortion_parameters: dict,
    device: Optional[torch.device] = None,
    x_normalized: Optional[torch.Tensor] = None,
    y_normalized: Optional[torch.Tensor] = None,
    r: Optional[torch.Tensor] = None,
) -> torch.Tensor:
    """Create a distorted sampling grid for barrel distortion."""
    if device is None:
        device = torch.device("cpu")
    if x_normalized is None or y_normalized is None or r is None:
        y, x = torch.meshgrid(
            torch.arange(H, device=device).float(),
            torch.arange(W, device=device).float(),
        )
        x_normalized = (x - K[0, 2]) / K[0, 0]
        y_normalized = (y - K[1, 2]) / K[1, 1]
        r = torch.sqrt(x_normalized**2 + y_normalized**2)
    else:
        x_normalized = x_normalized.to(device)
        y_normalized = y_normalized.to(device)
        r = r.to(device)
    k1, k2, k3, k4 = (distortion_parameters[k] for k in ["k1", "k2", "k3", "k4"])
    distortion_factor = 1 + k1 * r**2 + k2 * r**4 + k3 * r**6 + k4 * r**8
    x_distorted = x_normalized * distortion_factor
    y_distorted = y_normalized * distortion_factor
    x_distorted = x_distorted * K[0, 0] + K[0, 2]
    y_distorted = y_distorted * K[1, 1] + K[1, 2]
    x_distorted = 2 * (x_distorted / (W - 1)) - 1
    y_distorted = 2 * (y_distorted / (H - 1)) - 1
    grid = torch.stack([x_distorted, y_distorted], dim=-1).unsqueeze(0)
    return grid


def warp_image_gpu(
    image: torch.Tensor,
    K: torch.Tensor,
    distortion_parameters: dict,
    crop_percent: float = DEFAULT_CROP_PERCENT,
    x_normalized: Optional[torch.Tensor] = None,
    y_normalized: Optional[torch.Tensor] = None,
    r: Optional[torch.Tensor] = None,
) -> torch.Tensor:
    B, C, H, W = image.shape
    assert C == 3, "Input image should have 3 channels (RGB)"
    grid = make_distorted_grid(
        H,
        W,
        K,
        distortion_parameters,
        device=image.device,
        x_normalized=x_normalized,
        y_normalized=y_normalized,
        r=r,
    )
    grid = grid.repeat(B, 1, 1, 1)
    distorted_image = F.grid_sample(
        image, grid, mode="bilinear", padding_mode="zeros", align_corners=True
    )
    crop_h = int(H * crop_percent)
    crop_w = int(W * crop_percent)
    cropped_image = distorted_image[
        :,
        :,
        crop_h : -crop_h if crop_h > 0 else None,
        crop_w : -crop_w if crop_w > 0 else None,
    ]
    return cropped_image


class ApplyFullFisheyeWarping(torch.nn.Module):
    def __init__(self, H=None, W=None, K=None):
        super().__init__()
        self.H = H if H is not None else GOPRO_CAMERA_HEIGHT
        self.W = W if W is not None else GOPRO_CAMERA_WIDTH
        if K is None:
            K = calc_camera_intrinsics(GOPRO_VERTICAL_FOV, self.H, self.W)
        self.register_buffer("K", torch.tensor(K, dtype=torch.float32))
        self.register_buffer("x_normalized", None)
        self.register_buffer("y_normalized", None)
        self.register_buffer("r", None)
        self._precompute_values()

    def _precompute_values(self):
        device = self.K.device
        y, x = torch.meshgrid(
            torch.arange(self.H, device=device).float(),
            torch.arange(self.W, device=device).float(),
        )
        self.x_normalized = (x - self.K[0, 2]) / self.K[0, 0]
        self.y_normalized = (y - self.K[1, 2]) / self.K[1, 1]
        self.r = torch.sqrt(self.x_normalized**2 + self.y_normalized**2)

    def forward(self, img: torch.Tensor) -> torch.Tensor:
        device = img.device
        K = self.K.to(device)
        distortion_parameters = get_randomized_distortion_parameters()
        x_normalized = self.x_normalized.to(device)
        y_normalized = self.y_normalized.to(device)
        r = self.r.to(device)
        return warp_image_gpu(
            image=img,
            K=K,
            distortion_parameters=distortion_parameters,
            x_normalized=x_normalized,
            y_normalized=y_normalized,
            r=r,
        )


def _center_crop_to_4_3(frame_np: np.ndarray) -> np.ndarray:
    h, w = frame_np.shape[:2]
    target_w = h * 4 // 3
    if w <= target_w:
        return frame_np
    crop_left = (w - target_w) // 2
    return frame_np[:, crop_left : crop_left + target_w]


def warp_point_coordinates(
    points: np.ndarray,
    orig_h: int,
    orig_w: int,
    crop_percent: float = DEFAULT_CROP_PERCENT,
    n_iterations: int = 10,
) -> np.ndarray:
    if len(points) == 0:
        return points.copy()
    target_w = orig_h * 4 // 3
    if orig_w > target_w:
        crop_left = (orig_w - target_w) / 2.0
    else:
        target_w = orig_w
        crop_left = 0.0
    cropped_h = orig_h
    cropped_w = target_w
    K = calc_camera_intrinsics(GOPRO_VERTICAL_FOV, cropped_h, cropped_w)
    fx, fy = (K[0, 0], K[1, 1])
    cx, cy = (K[0, 2], K[1, 2])
    k1 = DEFAULT_DISTORTION_PARAMETERS["k1"]
    k2 = DEFAULT_DISTORTION_PARAMETERS["k2"]
    k3 = DEFAULT_DISTORTION_PARAMETERS["k3"]
    k4 = DEFAULT_DISTORTION_PARAMETERS["k4"]
    px = points[:, 0].astype(np.float64) * orig_w - crop_left
    py = points[:, 1].astype(np.float64) * orig_h
    in_x_norm = (px - cx) / fx
    in_y_norm = (py - cy) / fy
    out_x = in_x_norm.copy()
    out_y = in_y_norm.copy()
    for _ in range(n_iterations):
        r = np.sqrt(out_x**2 + out_y**2)
        D = 1.0 + k1 * r**2 + k2 * r**4 + k3 * r**6 + k4 * r**8
        out_x = in_x_norm / D
        out_y = in_y_norm / D
    out_px = out_x * fx + cx
    out_py = out_y * fy + cy
    crop_w_px = int(cropped_w * crop_percent)
    crop_h_px = int(cropped_h * crop_percent)
    out_px = out_px - crop_w_px
    out_py = out_py - crop_h_px
    final_w = cropped_w - 2 * crop_w_px
    final_h = cropped_h - 2 * crop_h_px
    norm_x = out_px / final_w
    norm_y = out_py / final_h
    valid = (norm_x >= 0) & (norm_x <= 1) & (norm_y >= 0) & (norm_y <= 1)
    result = np.stack([norm_x[valid], norm_y[valid]], axis=-1)
    return result.astype(np.float32)


_fisheye_warper: Optional[ApplyFullFisheyeWarping] = None


def apply_fisheye_warping(frame_np: np.ndarray) -> np.ndarray:
    global _fisheye_warper
    cropped_np = _center_crop_to_4_3(frame_np)
    h, w = cropped_np.shape[:2]
    img_tensor = (
        torch.from_numpy(cropped_np.copy()).float().permute(2, 0, 1).unsqueeze(0)
    )
    if _fisheye_warper is None or _fisheye_warper.H != h or _fisheye_warper.W != w:
        _fisheye_warper = ApplyFullFisheyeWarping(H=h, W=w)
    warped_tensor = _fisheye_warper(img_tensor)
    warped_np = warped_tensor.squeeze(0).permute(1, 2, 0).clamp(0, 255).byte().numpy()
    return warped_np


def get_randomized_distortion_parameters(
    distortion_parameters: Optional[dict] = None,
    randomization_factor: float = 0.001,
) -> dict:
    """Randomize distortion parameters with small uniform perturbations."""
    if distortion_parameters is None:
        distortion_parameters = DEFAULT_DISTORTION_PARAMETERS
    randomized = {}
    for key, value in distortion_parameters.items():
        randomized[key] = value + np.random.uniform(
            -randomization_factor, randomization_factor
        )
    return randomized
