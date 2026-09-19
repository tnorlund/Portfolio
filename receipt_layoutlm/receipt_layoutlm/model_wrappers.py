"""Shared LayoutLM wrappers for Core ML and Core AI export.

Keep a single logits-only forward signature so the two exporters cannot
silently diverge on inputs or outputs.
"""

from __future__ import annotations

import torch
from torch import nn


class LayoutLMWrapper(nn.Module):
    """Wrapper for LayoutLM v1 with explicit input order.

    Inputs: input_ids, attention_mask, bbox, token_type_ids.
    Output: logits tensor.
    """

    def __init__(self, model: nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        bbox: torch.Tensor,
        token_type_ids: torch.Tensor,
    ) -> torch.Tensor:
        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            bbox=bbox,
            token_type_ids=token_type_ids,
        )
        return outputs.logits


class LayoutLMv3Wrapper(nn.Module):
    """Wrapper for LayoutLMv3 export with image input.

    Accepts token_type_ids for a stable input signature (Swift always
    provides them) but does not forward them to the HF v3 model.
    """

    def __init__(self, model: nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        bbox: torch.Tensor,
        token_type_ids: torch.Tensor,
        pixel_values: torch.Tensor,
    ) -> torch.Tensor:
        outputs = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            bbox=bbox,
            pixel_values=pixel_values,
        )
        return outputs.logits
