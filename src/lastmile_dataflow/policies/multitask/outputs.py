from typing import NamedTuple, Optional, Dict, Tuple, List
import torch


class OLMoOutput(NamedTuple):
    logits: torch.Tensor
    attn_key_values: Optional[List[Tuple[torch.Tensor, torch.Tensor]]]
    hidden_states: Optional[Tuple[torch.Tensor, ...]]
    internal: Optional[Dict] = None
