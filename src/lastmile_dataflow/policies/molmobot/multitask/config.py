"""Read only the model section of a local RBY1 multitask checkpoint."""

from dataclasses import dataclass, fields
from enum import StrEnum as StrEnum
from pathlib import Path
from typing import Any

import yaml

D = Any


@dataclass
class BaseConfig:
    pass


@dataclass
class BaseModelConfig(BaseConfig):
    pass


@dataclass
class DataFormatter:
    message_format: str = "qwen3"
    system_prompt: str = "demo_or_style_v2"
    image_last: bool = False
    always_start_with_space: bool = False


@dataclass
class MultiModalConfig:
    image: Any = None
    use_frame_special_tokens: bool = True


def from_fields(cls, values, **nested):
    # Training options in the released YAML are not inputs to inference.
    names = {f.name for f in fields(cls) if f.init}
    return cls(**{k: v for k, v in {**values, **nested}.items() if k in names})


def load_model_config(checkpoint, tokenizer_path=None):
    from .model import MolmoBotConfig
    from .llm import LlmConfig
    from .vision import VitConfig
    from .connector import MolmoVisionBackboneConfig
    from .action_expert import ActionExpertConfig
    from .normalization import RobotProcessorConfig
    from .tokenizer import TokenizerConfig
    from .crops import MultiCropConfig

    raw = yaml.safe_load((Path(checkpoint) / "config.yaml").read_text())["model"]
    if raw["model_name"] not in ("molmoact", "molmobot"):
        raise ValueError("expected a MolmoBot multitask checkpoint")
    llm = from_fields(
        LlmConfig,
        raw["llm"],
        tokenizer=from_fields(TokenizerConfig, raw["llm"]["tokenizer"]),
    )
    if tokenizer_path:
        llm.tokenizer.identifier = str(tokenizer_path)
    vit = from_fields(VitConfig, raw["vision_backbone"]["vit"])
    image = from_fields(MultiCropConfig, raw["mm_preprocessor"]["image"])
    if (
        raw.get("cp_enabled", False)
        or llm.block_type != "sequential"
        or llm.attention_type != "sdpa"
        or vit.attention_type != "sdpa"
        or vit.image_model_type != "siglip"
        or image.crop_mode != "resize"
        or llm.rope_type != "default"
        or vit.resize_mode != "siglip"
        or vit.normalize != "siglip"
        or image.single_frame is not True
        or raw["n_obs_steps"] != 1
    ):
        raise ValueError(
            "this backend supports single-GPU SDPA/SigLIP multitask inference"
        )
    return from_fields(
        MolmoBotConfig,
        raw,
        llm=llm,
        vision_backbone=from_fields(
            MolmoVisionBackboneConfig, raw["vision_backbone"], vit=vit
        ),
        action_expert=from_fields(ActionExpertConfig, raw["action_expert"]),
        data_formatter=from_fields(DataFormatter, raw["data_formatter"]),
        mm_preprocessor=MultiModalConfig(
            image, raw["mm_preprocessor"]["use_frame_special_tokens"]
        ),
        robot_preprocessor=from_fields(RobotProcessorConfig, raw["robot_preprocessor"]),
        robot_postprocessor=from_fields(
            RobotProcessorConfig, raw["robot_postprocessor"]
        ),
    )
