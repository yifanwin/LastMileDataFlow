"""Single observation preprocessing and batching, without dataset/video loaders."""

import numpy as np
import torch
from . import tokenizer


class InferencePreprocessor:
    def __init__(self, config):
        self.config = config
        self.tokenizer = config.build_tokenizer()
        self.image, multi_image = config.mm_preprocessor.image.build_image_preprocessor(
            self.tokenizer, config.vision_backbone.build_preprocessor(), False
        )
        self.multi_image = multi_image

    def __call__(self, example):
        fmt = self.config.data_formatter
        if fmt.message_format != "qwen3" or fmt.system_prompt != "demo_or_style_v2":
            raise ValueError("expected the multitask Qwen3 demo prompt format")
        prompt = "demo: " + example["question"]
        if fmt.image_last and tokenizer.IMAGE_PROMPT not in prompt:
            prompt += tokenizer.IMAGE_PROMPT
        prompt = f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
        text = np.asarray(
            [self.tokenizer.bos_token_id or self.tokenizer.eos_token_id]
            + self.tokenizer.encode(prompt)
        )
        images = example["image"]
        if not isinstance(images, (list, tuple)):
            images = [images]
        data = self.multi_image(images) if len(images) > 1 else [self.image(images[0])]
        text_parts = []
        on = 0
        indices = np.flatnonzero(text == self.tokenizer.image_prompt_token_id)
        if not len(indices):
            indices = [1] * len(data)
        for i, ix in enumerate(indices):
            text_parts.extend([text[on:ix], data[i].tokens])
            on = ix + 1 if text[ix] == self.tokenizer.image_prompt_token_id else ix
        text_parts.append(text[on:])
        tokens = np.concatenate(text_parts)
        crops, pooling = [], []
        for item in data:
            offset = sum(np.prod(crop.shape[:2]) for crop in crops)
            pooling.append(
                np.where(
                    item.token_pooling >= 0,
                    item.token_pooling + offset,
                    item.token_pooling,
                )
            )
            crops.append(item.images)
        if len(tokens) > self.config.llm.max_sequence_length:
            raise ValueError("observation exceeds the model context length")
        return {
            "input_ids": tokens.astype(np.int64),
            "position_ids": np.arange(len(tokens), dtype=np.int64),
            "images": np.concatenate(crops),
            "token_pooling": np.concatenate(pooling),
            "states": np.asarray(example["state"], dtype=np.float32),
        }


def collate_observation(example, device):
    return {
        key: torch.from_numpy(np.asarray(value)[None]).to(device)
        for key, value in example.items()
    }
