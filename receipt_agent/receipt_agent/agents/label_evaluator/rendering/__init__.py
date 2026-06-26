"""Receipt rendering + font-geometry support for synthetic receipts.

This package turns a merchant's real receipts into a compact *font profile*
(typography + spacing geometry) and renders synthesized receipt dicts to PNG
images for visual QA and, later, LayoutLMv3 visual features.

Organizing principle (see CHARTER.md): the deterministic safety gates stay
deterministic. Everything here produces or consumes *geometry data*; it must
never relax the structure-similarity / arithmetic gates.
"""

from receipt_agent.agents.label_evaluator.rendering.font_profile import (
    MerchantFontProfile,
    ReceiptFontProfile,
    build_merchant_font_profile,
    build_merchant_font_profile_from_dynamo,
    extract_receipt_font_profile,
)
from receipt_agent.agents.label_evaluator.rendering.layoutlm_image import (
    DomainMatchContract,
    LayoutLMv3Example,
    LayoutLMv3ImageContract,
    apply_domain_match,
    normalize_pixels,
    synth_bbox_to_layoutlm,
    to_layoutlmv3_example,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
    RenderConfig,
    render_real_vs_synthetic,
    render_receipt,
    save_receipt_png,
)

__all__ = [
    "MerchantFontProfile",
    "ReceiptFontProfile",
    "build_merchant_font_profile",
    "build_merchant_font_profile_from_dynamo",
    "extract_receipt_font_profile",
    "RenderConfig",
    "render_receipt",
    "render_real_vs_synthetic",
    "save_receipt_png",
    "LayoutLMv3Example",
    "LayoutLMv3ImageContract",
    "DomainMatchContract",
    "apply_domain_match",
    "normalize_pixels",
    "synth_bbox_to_layoutlm",
    "to_layoutlmv3_example",
]
