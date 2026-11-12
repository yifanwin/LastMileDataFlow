from __future__ import annotations
import torch


def ensure_finite_(
    x: torch.Tensor, check_neg_inf: bool = True, check_pos_inf: bool = False
):
    """
    Modify ``x`` in place to replace ``float("-inf")`` with the minimum value of the dtype when ``check_neg_inf``
    is ``True`` and to replace ``float("inf")`` with the maximum value of the dtype when ``check_pos_inf`` is ``True``.
    """
    if check_neg_inf:
        x.masked_fill_(x == float("-inf"), torch.finfo(x.dtype).min)
    if check_pos_inf:
        x.masked_fill_(x == float("inf"), torch.finfo(x.dtype).max)


def get_default_device() -> torch.device:
    if torch.cuda.is_available() and torch.cuda.is_initialized():
        return torch.device("cuda")
    else:
        return torch.device("cpu")


def freeze_module(module):
    module.requires_grad_(False)


class BufferCache(dict):
    """Device-local attention buffers, kept outside the parameter state."""
