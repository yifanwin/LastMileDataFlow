from __future__ import annotations
import dataclasses
from typing import Optional, Dict
import numpy as np

TOKEN_POOLING_KEYS = ["token_pooling", "low_res_token_pooling"]


@dataclasses.dataclass
class TokenizedVisionData:
    """Data for visual input that has been converted into tokens"""

    tokens: np.ndarray
    "Token ids to use in the LLM"
    images: np.ndarray
    "Images in [n_images, n_patches, patch_size] format"
    image_masks: Optional[np.ndarray] = None
    "Images in [n_images, n_patches] format"
    token_pooling: Optional[np.ndarray] = None
    "[n_visual_tokens, pooling_dim] array of of how to pool ViT patches for patch_id tokens"
    low_res_token_pooling: Optional[np.ndarray] = None
    "[n_visual_tokens, pooling_dim] array of of how to pool ViT patches for low_res tokens"
    position_ids: Optional[np.ndarray] = None
    "Position id for each token if not sequential"
    other_data: Optional[Dict[str, np.ndarray]] = None
    "Any extra data that needs to be passed to the model"


def batch_pixels_to_patches(array, patch_size):
    """Reshape images of [n_images, h, w, 3] -> [n_images, n_patches, pixels_per_patch]"""
    if len(array.shape) == 3:
        n_crops, h, w = array.shape
        h_patches = h // patch_size
        w_patches = w // patch_size
        array = np.reshape(
            array, [n_crops, h_patches, patch_size, w_patches, patch_size]
        )
        array = np.transpose(array, [0, 1, 3, 2, 4])
        array = np.reshape(
            array, [n_crops, h_patches * w_patches, patch_size * patch_size]
        )
        return array
    else:
        n_crops, h, w, c = array.shape
        h_patches = h // patch_size
        w_patches = w // patch_size
        array = np.reshape(
            array, [n_crops, h_patches, patch_size, w_patches, patch_size, c]
        )
        array = np.transpose(array, [0, 1, 3, 2, 4, 5])
        array = np.reshape(
            array, [n_crops, h_patches * w_patches, patch_size * patch_size * c]
        )
        return array
