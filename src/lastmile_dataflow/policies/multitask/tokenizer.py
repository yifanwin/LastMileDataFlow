from dataclasses import dataclass
from typing import List, Optional
from transformers import AutoTokenizer
from .config import BaseConfig
from functools import cache

IMAGE_PATCH_TOKEN = f"<im_patch>"
IMAGE_LOW_RES_TOKEN = f"<im_low>"
IM_START_TOKEN = f"<im_start>"
LOW_RES_IMAGE_START_TOKEN = f"<low_res_im_start>"
FRAME_START_TOKEN = f"<frame_start>"
IM_END_TOKEN = f"<im_end>"
FRAME_END_TOKEN = f"<frame_end>"
IM_COL_TOKEN = f"<im_col>"
IMAGE_PROMPT = "<|image|>"
VIDEO_PROMPT = "<|video|>"
EXTRA_TOKENS = (
    IM_START_TOKEN,
    IM_END_TOKEN,
    IMAGE_PATCH_TOKEN,
    IM_COL_TOKEN,
    LOW_RES_IMAGE_START_TOKEN,
    IMAGE_PROMPT,
    IMAGE_LOW_RES_TOKEN,
    FRAME_START_TOKEN,
    FRAME_END_TOKEN,
    VIDEO_PROMPT,
)


class HfTokenizerWrapper:
    """Tokenizer wrapper

    This exists mostly for legacy reasons since we used to support other kinds of tokenizers
    with different APIs
    """

    def __init__(self, tokenizer, bos_token_id=None, adds_space=False):
        self.adds_space = adds_space
        self.tokenizer = tokenizer
        if bos_token_id is None:
            self.bos_token_id = tokenizer.bos_token_id
        else:
            self.bos_token_id = bos_token_id
        self.eos_token_id = self.tokenizer.eos_token_id
        self.pad_id = -1
        special_tokens = get_special_token_ids(self)
        self.image_end_token_id = special_tokens[IM_END_TOKEN]
        self.image_start_token_id = special_tokens[IM_START_TOKEN]
        self.low_res_image_start_token_id = special_tokens[LOW_RES_IMAGE_START_TOKEN]
        self.frame_start_token_id = special_tokens[FRAME_START_TOKEN]
        self.frame_end_token_id = special_tokens[FRAME_END_TOKEN]
        self.image_col_token_id = special_tokens[IM_COL_TOKEN]
        self.image_patch_token_id = special_tokens[IMAGE_PATCH_TOKEN]
        self.image_low_res_token_id = special_tokens[IMAGE_LOW_RES_TOKEN]
        self.image_prompt_token_id = special_tokens[IMAGE_PROMPT]
        self.frame_start_token_id = special_tokens[FRAME_START_TOKEN]
        self.frame_end_token_id = special_tokens[FRAME_END_TOKEN]
        self.video_prompt_token_id = special_tokens[VIDEO_PROMPT]

    def encode(self, x: str):
        return self.tokenizer.encode(x, add_special_tokens=False)

    def decode(self, x: List[int], truncate_at_eos=True):
        x = [int(t) for t in x]
        if self.eos_token_id == self.bos_token_id and (
            len(x) > 0 and x[0] == self.eos_token_id
        ):
            x = x[1:]
        if truncate_at_eos:
            try:
                eos_ix = x.index(self.eos_token_id)
                x = x[:eos_ix]
            except ValueError:
                pass
        else:
            x = [t for t in x if t != self.eos_token_id and t != self.bos_token_id]
        return self.tokenizer.decode(x)

    def vocab_size(self):
        return len(self.tokenizer)


def build_tokenizer(
    tokenizer_type, has_extra_token=True, tokenizer_dir=None, pad_tokenizer_to=None
):
    return _build_tokenizer(tokenizer_type, tokenizer_dir, pad_tokenizer_to)


@cache
def _build_tokenizer(identifier, cache_dir, pad_tokenizer_to):
    tok = AutoTokenizer.from_pretrained(identifier, cache_dir=cache_dir)
    extra_tokens = list(EXTRA_TOKENS)
    if pad_tokenizer_to is not None:
        n = pad_tokenizer_to - len(tok)
        extra_tokens = [f"|<EXTRA_TOKENS_{i}>|" for i in range(n)] + extra_tokens
    tok.add_special_tokens({"additional_special_tokens": extra_tokens})
    return HfTokenizerWrapper(tok, bos_token_id=tok.bos_token_id or tok.eos_token_id)


def get_special_token_ids(tokenizer):
    if isinstance(tokenizer, HfTokenizerWrapper):
        ids = tokenizer.encode("".join(EXTRA_TOKENS))
        if len(ids) == len(EXTRA_TOKENS) + 1:
            ids = ids[1:]
    else:
        ids = tokenizer.encode(" ".join(EXTRA_TOKENS))
    assert len(ids) == len(EXTRA_TOKENS)
    return {k: i for k, i in zip(EXTRA_TOKENS, ids)}


@dataclass
class TokenizerConfig(BaseConfig):
    identifier: str = "gpt2"
    tokenizer_dir: Optional[str] = None

    def build(self, pad_tokenizer_to):
        return build_tokenizer(
            self.identifier,
            tokenizer_dir=self.tokenizer_dir,
            pad_tokenizer_to=pad_tokenizer_to,
        )
