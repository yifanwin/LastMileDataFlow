"""SigLIP preprocessing for in-memory RGB images."""

from dataclasses import dataclass
from typing import Tuple
import numpy as np
import torch
import torchvision
from torchvision.transforms import InterpolationMode


def siglip_resize_and_pad(
    image: np.ndarray, desired_output_size: Tuple[int, int], float32=True
) -> Tuple[np.ndarray, np.ndarray]:
    in_min = 0.0
    in_max = 255.0

    if len(image.shape) == 3:
        is_video = False
        image = torch.permute(torch.from_numpy(image), [2, 0, 1])
    else:
        is_video = True
        image = torch.permute(torch.from_numpy(image), [0, 3, 1, 2])
    dtype = image.dtype
    if torch.is_floating_point(image):
        resized = torchvision.transforms.Resize(
            desired_output_size,
            InterpolationMode.BILINEAR,
            antialias=False,
        )(image)
        resized = torch.clip(resized, 0.0, 1.0).to(dtype)
    else:
        assert image.dtype == torch.uint8, (
            "SigLIP expects float images or uint8 images, but got {}".format(
                image.dtype
            )
        )
        resized = torchvision.transforms.Resize(
            desired_output_size,
            InterpolationMode.BILINEAR,
            antialias=False,
        )(image)
        resized = torch.clip(resized, 0, 255).to(dtype)

    if float32:
        resized = resized.to(torch.float32)
        resized = (resized - in_min) / (in_max - in_min)

    if is_video:
        resized = torch.permute(resized, [0, 2, 3, 1]).numpy()
        image_mask = None
    else:
        resized = torch.permute(resized, [1, 2, 0]).numpy()
        image_mask = np.ones_like(resized[:, :, 0], dtype=np.bool_)

    return resized, image_mask


@dataclass
class ImagePreprocessor:
    normalize: str = "siglip"
    resize: str = "siglip"
    pad_value: float = 0
    image_patch_size: int = 14
    base_image_input_size: Tuple[int, int] = (378, 378)
    use_image_mask: bool = False
    normalize_on_gpu: bool = True

    def normalize_image_tensor(self, image):
        if image.dtype == torch.uint8:
            image = image.float() / 255.0
        return image * 2 - 1

    def build_single_crop(self, image, is_training=False, rng=None, image_size=None):
        image_size = image_size or self.base_image_input_size
        resized, mask = siglip_resize_and_pad(
            image, image_size, float32=not self.normalize_on_gpu
        )
        if not self.normalize_on_gpu:
            resized = np.asarray(-1.0, dtype=np.float32) + resized * np.asarray(
                2.0, dtype=np.float32
            )
        if resized.ndim == 3:
            resized = resized[None]
        h, w = (n // self.image_patch_size for n in image_size)
        return (
            resized,
            mask[None] if self.use_image_mask else None,
            np.arange(h * w).reshape(h, w),
        )
