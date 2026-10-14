from __future__ import annotations
import dataclasses
from typing import List, Union, Tuple, Optional
import PIL
from PIL import ImageFile
from einops import einops
from .config import BaseConfig
from .images import ImagePreprocessor
from .tensors import TokenizedVisionData, batch_pixels_to_patches
from .tokenizer import HfTokenizerWrapper
import numpy as np
from transformers.image_utils import ImageInput


def setup_pil():
    PIL.Image.MAX_IMAGE_PIXELS = None
    ImageFile.LOAD_TRUNCATED_IMAGES = True


def arange_for_pooling(idx_arr, pool_h, pool_w):
    h_pad = pool_h * ((idx_arr.shape[0] + pool_h - 1) // pool_h) - idx_arr.shape[0]
    w_pad = pool_w * ((idx_arr.shape[1] + pool_w - 1) // pool_w) - idx_arr.shape[1]
    idx_arr = np.pad(
        idx_arr,
        [[h_pad // 2, (h_pad + 1) // 2], [w_pad // 2, (w_pad + 1) // 2]],
        mode="constant",
        constant_values=-1,
    )
    return einops.rearrange(
        idx_arr, "(h dh) (w dw) -> h w (dh dw)", dh=pool_h, dw=pool_w
    )


@dataclasses.dataclass
class MultiCropConfig(BaseConfig):
    crop_mode: str = "resize"
    use_col_tokens: bool = True
    max_crops: int = 6
    high_res_max_crops: int = 24
    p_high_res: float = 0
    pooling_w: int = 2
    pooling_h: int = 2
    overlap_margins: Tuple[int, int] = (4, 4)
    max_images: Optional[int] = None
    max_multi_image_crops: int = 4
    multi_image_pooling_w: int = 2
    multi_image_pooling_h: int = 2
    use_single_crop_col_tokens: Optional[bool] = None
    use_single_crop_start_token: bool = False
    single_frame: bool = True

    def build_image_preprocessor(
        self, tokenizer, image_preprocessor, image_padding_mask, legacy_image_mask=False
    ):
        image = MultiCropImagePreprocessor(
            tokenizer,
            image_preprocessor,
            legacy_image_mask,
            self.crop_mode,
            self.use_col_tokens,
            self.max_crops,
            self.high_res_max_crops,
            self.p_high_res,
            self.pooling_w,
            self.pooling_h,
            image_padding_mask,
            self.overlap_margins,
            use_single_crop_col_tokens=self.use_single_crop_col_tokens,
            use_single_crop_start_token=self.use_single_crop_start_token,
            single_frame=self.single_frame,
        )
        if self.max_images is not None:
            multi_image = MultiImagePreprocessor(
                dataclasses.replace(
                    image,
                    max_crops=self.max_multi_image_crops,
                    image_pooling_w=self.multi_image_pooling_w,
                    image_pooling_h=self.multi_image_pooling_h,
                    p_high_res=0,
                ),
                self.max_images,
            )
        else:
            multi_image = None
        return (image, multi_image)


@dataclasses.dataclass
class MultiCropImagePreprocessor:
    tokenizer: HfTokenizerWrapper
    image_preprocessor: ImagePreprocessor
    legacy_image_mask: bool = False
    crop_mode: str = "resize"
    use_col_tokens: bool = True
    max_crops: int = 6
    high_res_max_crops: int = 12
    p_high_res: 0 = 0
    image_pooling_w: int = 2
    image_pooling_h: int = 2
    image_padding_mask: Union[bool, int] = False
    overlap_margins: Tuple[int, int] = (4, 4)
    use_single_crop_col_tokens: Optional[bool] = None
    use_single_crop_start_token: bool = False
    single_frame: bool = True

    def __call__(
        self, image: ImageInput, is_training=False, rng=np.random
    ) -> TokenizedVisionData:
        pooling_h = self.image_pooling_h
        pooling_w = self.image_pooling_w
        patch_id = self.tokenizer.image_patch_token_id
        base_image_input_size = self.image_preprocessor.base_image_input_size
        image_patch_size = self.image_preprocessor.image_patch_size
        if self.use_single_crop_col_tokens is None:
            use_single_crop_col_tokens = self.use_col_tokens
        else:
            use_single_crop_col_tokens = self.use_single_crop_col_tokens
        if isinstance(base_image_input_size, int):
            base_image_input_size = (base_image_input_size, base_image_input_size)
        base_image_input_d = image_patch_size
        crop_patch_w = base_image_input_size[1] // base_image_input_d
        crop_patch_h = base_image_input_size[0] // base_image_input_d
        original_image_h, original_image_w = image.shape[:2]
        crop_size = base_image_input_size[0]
        resized, resized_mask, resize_idx = self.image_preprocessor.build_single_crop(
            image, is_training=is_training, rng=rng
        )
        resize_idx = np.arange(crop_patch_w * crop_patch_h).reshape(
            [crop_patch_h, crop_patch_w]
        )
        pooling_idx = arange_for_pooling(resize_idx, pooling_h, pooling_w)
        h, w = pooling_idx.shape[:2]
        pooling_idx = pooling_idx.reshape([-1, pooling_h * pooling_w])
        per_row = np.full((w,), patch_id, dtype=np.int32)
        if use_single_crop_col_tokens:
            per_row = np.concatenate([per_row, [self.tokenizer.image_col_token_id]], 0)
        extra_tokens = np.tile(per_row, [h])
        joint = [
            [self.tokenizer.image_start_token_id],
            extra_tokens,
            [self.tokenizer.image_end_token_id],
        ]
        return TokenizedVisionData(
            tokens=np.concatenate(joint, 0),
            images=batch_pixels_to_patches(resized, image_patch_size),
            image_masks=None
            if resized_mask is None
            else batch_pixels_to_patches(resized_mask, image_patch_size).mean(-1),
            token_pooling=pooling_idx,
        )


@dataclasses.dataclass
class MultiImagePreprocessor:
    image_preprocessor: MultiCropImagePreprocessor
    max_images: int

    def __call__(
        self, images, is_training=False, rng=None
    ) -> List[TokenizedVisionData]:
        tokenized_images = []
        assert len(images) > 1, "Number of images must be at least 2"
        assert len(images) >= 2
        for idx, image in enumerate(images):
            image_data = self.image_preprocessor(image, is_training, rng)
            prefix = f"Image {idx + 1}"
            image_data.tokens = np.concatenate(
                [self.image_preprocessor.tokenizer.encode(prefix), image_data.tokens], 0
            )
            assert image_data.position_ids is None
            tokenized_images.append(image_data)
            if self.max_images is not None and idx == self.max_images - 1:
                break
        return tokenized_images
