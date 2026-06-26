"""Shape a synthesized receipt for LayoutLMv3, and document the image contract.

This is the *training-image contract* for milestone 4 of the
``feat/receipt-font-render`` charter. It does NOT wire training.

**Scope (read this first).** The structured example — tokens + bounding boxes +
labels — is the PRIMARY, canonical training artifact. It is domain-independent:
boxes and labels carry no photo-vs-render distribution gap, so the structured
columns are training-ready as soon as their coordinates match LayoutLMv3's frame.

Rendered images are **QA-first**. A clean render is NOT a LayoutLMv3 training
image: real receipts are photographs (thermal fade, skew, crumple, uneven
lighting, sensor noise), so a pristine render is a *different distribution*.
Training v3's visual backbone on clean renders risks a domain gap that can hurt
the model. An image becomes "training-ready" only after it is **domain-matched**
to the receipt-photo distribution (degraded and/or composited onto real
backgrounds). This module documents and stubs that contract
(:class:`DomainMatchContract`, :func:`apply_domain_match`) rather than wiring
clean renders into training. QA rendering stays fully unblocked.

Two things this module pins for the structured artifact:

1. **Token-box alignment.** LayoutLMv3 expects token boxes as integers in
   ``[0,1000]`` with a **top-left origin, y increasing downward**. The synthesis
   space is ``[0,1000]`` but **y high-is-top**, so the y axis must be flipped.
   Getting this wrong silently misaligns every box — the most common LayoutLMv3
   bug. This conversion is domain-independent and always valid.
2. **Image + normalization contract.** LayoutLMv3's visual backbone takes a
   square RGB image (default 224×224), rescaled to ``[0,1]`` then normalized with
   per-channel mean/std (:class:`LayoutLMv3ImageContract`). Pixel normalization
   is normally done by HuggingFace's ``LayoutLMv3ImageProcessor``
   (``apply_ocr=False``); we expose the same constants and an optional
   pure-Python/NumPy normalizer so the contract is verifiable without importing
   transformers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from PIL import Image

from receipt_agent.agents.label_evaluator.rendering.font_profile import (
    MerchantFontProfile,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
    RenderConfig,
    render_receipt,
)

# LayoutLMv3 token boxes are integers in this range (HF LayoutLMv3 convention).
LAYOUTLM_BBOX_SCALE = 1000

# A domain-matching transform: clean render + its LayoutLM boxes -> a
# photo-distribution image AND the boxes moved to stay aligned with it. It is
# BOX-AWARE on purpose: geometric transforms (skew/warp/crumple) move pixels, so
# the boxes must move with them or the pair is misaligned. Supplied by the
# (not-yet-built) degradation/compositing pipeline; until one exists the image
# stays a QA render and is NOT training-ready.
LayoutLMBox = tuple[int, int, int, int]
DomainMatchTransform = Callable[
    [Image.Image, Sequence[LayoutLMBox]],
    "tuple[Image.Image, Sequence[LayoutLMBox]]",
]


@dataclass(frozen=True)
class DomainMatchContract:
    """What a render must undergo before it is a LayoutLMv3 *training* image.

    Real receipts are photographs; a clean render is a different distribution.
    Each flag names a degradation a training-ready image must carry to close the
    gap. This is a CONTRACT (documentation + a checklist), not an implementation:
    the actual transforms live in a future degradation/compositing pipeline.
    Until such a pipeline runs, an image is QA-only and never training-ready.
    """

    # Geometric: photographed receipts are skewed/rotated and physically curled.
    skew_rotation: bool = True
    perspective_warp: bool = True
    crumple_warp: bool = True
    # Photometric: thermal paper fades; lighting is uneven; capture adds noise.
    thermal_fade: bool = True
    lighting_gradient: bool = True
    sensor_noise: bool = True
    jpeg_artifacts: bool = True
    # Context: the receipt sits on a real surface, not a clean canvas.
    background_composite: bool = True

    def required_transforms(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, enabled in (
                ("skew_rotation", self.skew_rotation),
                ("perspective_warp", self.perspective_warp),
                ("crumple_warp", self.crumple_warp),
                ("thermal_fade", self.thermal_fade),
                ("lighting_gradient", self.lighting_gradient),
                ("sensor_noise", self.sensor_noise),
                ("jpeg_artifacts", self.jpeg_artifacts),
                ("background_composite", self.background_composite),
            )
            if enabled
        )

    def describe(self) -> dict[str, Any]:
        return {
            "required_transforms": list(self.required_transforms()),
            "rationale": (
                "Real receipts are photographs (thermal fade, skew, crumple, "
                "lighting, sensor noise). A clean render is a different "
                "distribution; LayoutLMv3's visual backbone must train on "
                "domain-matched images or risk a domain gap."
            ),
            "note": (
                "The structured example (tokens + boxes + labels) is the "
                "canonical, domain-independent training artifact. Only the "
                "IMAGE needs domain matching before training."
            ),
            "bbox_invariance": (
                "Geometric domain-match transforms (skew/warp/crumple) move the "
                "image; the SAME transform must be applied to the token boxes to "
                "keep image/box alignment. apply_domain_match leaves boxes "
                "unchanged, so image-moving transforms require a box-aware "
                "pipeline before training."
            ),
        }


def apply_domain_match(
    image: Image.Image,
    boxes: Sequence[LayoutLMBox],
    *,
    transform: DomainMatchTransform | None = None,
    contract: DomainMatchContract | None = None,
) -> "tuple[Image.Image, list[LayoutLMBox]]":
    """Apply a box-aware domain-matching transform to a clean render. STUB.

    Pass ``transform`` (the future degradation/compositing pipeline) to turn a
    clean render into a photo-distribution image AND move its token boxes so the
    pair stays aligned. With no ``transform`` this raises
    :class:`NotImplementedError` ON PURPOSE — it must never silently return a
    clean render and let a caller treat it as training-ready. The ``contract``
    argument documents what the transform is expected to do.

    The transform is box-aware precisely because the contract includes geometric
    transforms (skew/warp/crumple): an image-only transform would misalign the
    boxes while still appearing "matched".
    """
    if transform is None:
        required = (contract or DomainMatchContract()).required_transforms()
        raise NotImplementedError(
            "No domain-match transform supplied; a clean render is QA-only and "
            "must not be used as a LayoutLMv3 training image. Required "
            f"transforms before training: {', '.join(required)}."
        )
    matched_image, matched_boxes = transform(image, boxes)
    return matched_image, list(matched_boxes)


@dataclass(frozen=True)
class LayoutLMv3ImageContract:
    """The visual-input contract for LayoutLMv3.

    The defaults mirror the **transformers 4.x** ``LayoutLMv3ImageProcessor``
    (mean/std ``0.5``, bicubic). NOTE: transformers 5.x changed the LayoutLMv3
    image defaults to ImageNet mean/std + bilinear, so these constants can
    diverge from the model you actually train. For training, build the contract
    from the exact processor with :meth:`from_hf_processor` rather than trusting
    these defaults, and persist it alongside the dataset.
    """

    image_size: int = 224
    rescale_factor: float = 1.0 / 255.0
    image_mean: tuple[float, float, float] = (0.5, 0.5, 0.5)
    image_std: tuple[float, float, float] = (0.5, 0.5, 0.5)
    resample: str = "bicubic"
    bbox_scale: int = LAYOUTLM_BBOX_SCALE
    # The renderer / LayoutLMv3 image is top-left origin, y increasing downward.
    image_axis: str = "top_left_y_down"
    # The synthesis receipt space is y high-is-top, so y is flipped on convert.
    source_axis: str = "bottom_left_y_up_0_1000"

    @classmethod
    def from_hf_processor(cls, processor: Any) -> "LayoutLMv3ImageContract":
        """Derive the contract from a HF ``LayoutLMv3ImageProcessor`` instance.

        Reads ``size``/``image_mean``/``image_std``/``rescale_factor``/``resample``
        off the actual processor so the synthetic image pipeline cannot diverge
        from the model's preprocessing across ``transformers`` versions. Missing
        attributes fall back to the class defaults.
        """
        size = getattr(processor, "size", None)
        if isinstance(size, Mapping):
            image_size = int(size.get("height") or size.get("shortest_edge") or 224)
        else:
            image_size = int(size or 224)
        mean = getattr(processor, "image_mean", None) or cls.image_mean
        std = getattr(processor, "image_std", None) or cls.image_std
        return cls(
            image_size=image_size,
            rescale_factor=float(
                getattr(processor, "rescale_factor", cls.rescale_factor)
            ),
            image_mean=tuple(float(v) for v in mean),  # type: ignore[arg-type]
            image_std=tuple(float(v) for v in std),  # type: ignore[arg-type]
            resample=str(getattr(processor, "resample", cls.resample)),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "image_size": self.image_size,
            "rescale_factor": self.rescale_factor,
            "image_mean": list(self.image_mean),
            "image_std": list(self.image_std),
            "resample": self.resample,
            "bbox_scale": self.bbox_scale,
            "image_axis": self.image_axis,
            "source_axis": self.source_axis,
            "apply_ocr": False,
            "notes": (
                "Token boxes are flipped from y-high-is-top synthesis space to "
                "LayoutLMv3 top-left/y-down 0-1000 so they align with the image."
            ),
        }


@dataclass(frozen=True)
class LayoutLMv3Example:
    """One synthetic example shaped for LayoutLMv3.

    The CANONICAL training artifact is the structured triple
    ``tokens`` / ``bboxes`` / ``ner_tags``: integer ``[x0,y0,x1,y1]`` boxes in
    ``[0,bbox_scale]`` (top-left/y-down), labels, and tokens. These are
    domain-independent and training-ready.

    ``image`` is the rendered RGB receipt at the contract's target size. It is
    **QA-first**. Unless ``domain_matched`` is True (a degradation/compositing
    transform was applied to match the receipt-photo distribution), the image is
    a clean render and must NOT be fed to LayoutLMv3's visual backbone — see
    :class:`DomainMatchContract`. Check :attr:`image_training_ready` before using
    the image for training.
    """

    image: Image.Image
    tokens: tuple[str, ...]
    bboxes: tuple[tuple[int, int, int, int], ...]
    ner_tags: tuple[Any, ...]
    contract: LayoutLMv3ImageContract = field(
        default_factory=LayoutLMv3ImageContract
    )
    domain_matched: bool = False

    @property
    def image_training_ready(self) -> bool:
        """The image may be used for training only once domain-matched."""
        return self.domain_matched

    def to_dict(self, *, include_image: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tokens": list(self.tokens),
            "bboxes": [list(box) for box in self.bboxes],
            "ner_tags": list(self.ner_tags),
            "contract": self.contract.describe(),
            "image_size": list(self.image.size),
            "domain_matched": self.domain_matched,
            "image_role": "training" if self.domain_matched else "qa_only",
        }
        if include_image:
            payload["image"] = self.image
        return payload


def synth_bbox_to_layoutlm(
    bbox: Sequence[float],
    *,
    coord_max: float = float(LAYOUTLM_BBOX_SCALE),
    scale: int = LAYOUTLM_BBOX_SCALE,
) -> tuple[int, int, int, int] | None:
    """Convert a y-high-is-top synthesis box to a LayoutLMv3 0-1000 y-down box.

    The synthesis box is ``[x0,y0,x1,y1]`` with larger y nearer the top. The
    LayoutLMv3 box is integer ``[x0,y0,x1,y1]`` in ``[0,scale]`` with y0 the top
    and y increasing downward — i.e. ``y_layoutlm = scale - y_synth``. Returns
    ``None`` for a degenerate/non-finite box.
    """
    if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes)):
        return None
    if len(bbox) < 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):  # NaN / inf
        return None
    if coord_max <= 0:
        return None

    def sx(value: float) -> int:
        return _clamp_int(value / coord_max * scale, scale)

    left, right = sorted((sx(x0), sx(x1)))
    # Flip y: top edge is the larger synthesis y -> smaller LayoutLM y.
    top_synth, bottom_synth = max(y0, y1), min(y0, y1)
    top = _clamp_int((coord_max - top_synth) / coord_max * scale, scale)
    bottom = _clamp_int((coord_max - bottom_synth) / coord_max * scale, scale)
    if bottom < top:
        top, bottom = bottom, top
    # Reject boxes that collapse to zero area after scaling/clamping (e.g.
    # off-canvas tokens whose coords clamp to the same edge).
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def to_layoutlmv3_example(
    example: Mapping[str, Any],
    *,
    profile: MerchantFontProfile | None = None,
    contract: LayoutLMv3ImageContract | None = None,
    coord_max: float | None = None,
    domain_match: DomainMatchTransform | None = None,
) -> LayoutLMv3Example:
    """Shape a synthetic example for LayoutLMv3.

    ``example`` is the synthesis bundle shape: ``{"tokens", "bboxes",
    "ner_tags"}`` with ``bboxes`` in 0-1000 y-high-is-top (or a receipt dict with
    ``words``). Produces the canonical structured columns (tokens, top-left/y-down
    0-1000 boxes, labels) plus a rendered image.

    The structured columns are training-ready. The image is **QA-first**: it is a
    clean render unless ``domain_match`` (a box-aware degradation/compositing
    transform, ``(image, boxes) -> (image, boxes)``) is supplied, in which case it
    is applied to the image AND its boxes together and the result is flagged
    ``domain_matched=True``. Without ``domain_match`` the returned example's image
    is NOT training-ready (see :class:`DomainMatchContract`). Because the
    transform is box-aware, geometric domain transforms (skew/warp/crumple) keep
    image and boxes aligned.
    """
    contract = contract or LayoutLMv3ImageContract()
    tokens, raw_boxes, tags = _extract_columns(example)
    source_scale = coord_max or _detect_scale(raw_boxes)

    # Render the receipt at the visual-backbone's square target. The renderer
    # already flips y for display, so the image is top-down — matching the boxes
    # we emit below. margin=0 is REQUIRED: the emitted LayoutLM boxes span the
    # full 0-1000 frame, so the rendered pixels must too (a non-zero margin
    # insets/scales the pixels and silently misaligns them from the boxes). A
    # square canvas matches LayoutLMv3's square input; the resize below is a
    # no-op safeguard if the renderer size ever changes.
    render_config = RenderConfig(
        width=contract.image_size,
        height=contract.image_size,
        margin=0,
        color_by_label=False,
    )
    receipt_for_render = {"words": [
        {"text": token, "bbox": box}
        for token, box in zip(tokens, raw_boxes)
    ]}
    image = render_receipt(
        receipt_for_render,
        profile=profile,
        config=render_config,
        coord_max=source_scale,
    )
    if image.size != (contract.image_size, contract.image_size):
        image = image.resize(
            (contract.image_size, contract.image_size), Image.BICUBIC
        )

    out_tokens: list[str] = []
    out_boxes: list[tuple[int, int, int, int]] = []
    out_tags: list[Any] = []
    for index, (token, box) in enumerate(zip(tokens, raw_boxes)):
        converted = synth_bbox_to_layoutlm(
            box, coord_max=source_scale, scale=contract.bbox_scale
        )
        if converted is None:
            continue
        out_tokens.append(token)
        out_boxes.append(converted)
        out_tags.append(tags[index] if index < len(tags) else None)

    # Domain matching is the ONLY way the image becomes training-ready. It is
    # box-aware so geometric transforms move the image AND its boxes together.
    # Without a transform the image stays a clean QA render (domain_matched=False).
    domain_matched = False
    if domain_match is not None:
        image, matched = apply_domain_match(
            image, out_boxes, transform=domain_match
        )
        out_boxes = list(matched)
        domain_matched = True

    return LayoutLMv3Example(
        image=image,
        tokens=tuple(out_tokens),
        bboxes=tuple(out_boxes),
        ner_tags=tuple(out_tags),
        contract=contract,
        domain_matched=domain_matched,
    )


def normalize_pixels(
    image: Image.Image, contract: LayoutLMv3ImageContract | None = None
) -> Any:
    """Rescale + normalize an image to LayoutLMv3's pixel statistics.

    Returns a NumPy array of shape ``(3, H, W)`` when NumPy is available
    (channels-first, the LayoutLMv3 layout); otherwise a nested Python list of
    the same shape. This mirrors what HF's ``LayoutLMv3ImageProcessor`` produces
    and exists so the contract is verifiable without importing transformers.
    """
    contract = contract or LayoutLMv3ImageContract()
    rgb = image.convert("RGB")
    width, height = rgb.size
    pixels = list(rgb.getdata())  # row-major list of (r,g,b)
    try:
        import numpy as np

        arr = np.asarray(pixels, dtype="float32").reshape(height, width, 3)
        arr = arr * contract.rescale_factor
        mean = np.asarray(contract.image_mean, dtype="float32")
        std = np.asarray(contract.image_std, dtype="float32")
        arr = (arr - mean) / std
        return arr.transpose(2, 0, 1)  # HWC -> CHW
    except ImportError:
        channels = [
            [[0.0] * width for _ in range(height)] for _ in range(3)
        ]
        for idx, (r, g, b) in enumerate(pixels):
            y, x = divmod(idx, width)
            for c, value in enumerate((r, g, b)):
                scaled = value * contract.rescale_factor
                channels[c][y][x] = (
                    scaled - contract.image_mean[c]
                ) / contract.image_std[c]
        return channels


def _extract_columns(
    example: Mapping[str, Any],
) -> tuple[list[str], list[Any], list[Any]]:
    tokens = example.get("tokens")
    bboxes = example.get("bboxes")
    if isinstance(tokens, list) and isinstance(bboxes, list):
        tags = example.get("ner_tags") or []
        return list(tokens), list(bboxes), list(tags)
    # Receipt-dict shape: pull words in reading order.
    words: list[Mapping[str, Any]] = []
    if isinstance(example.get("words"), list):
        words = [w for w in example["words"] if isinstance(w, Mapping)]
    else:
        for line in example.get("lines", []) or []:
            for word in line.get("words", []) or []:
                if isinstance(word, Mapping):
                    words.append(word)
    out_tokens = [str(w.get("text") or "") for w in words]
    out_boxes = [w.get("bbox") for w in words]
    out_tags = [(w.get("labels") or [None])[0] for w in words]
    return out_tokens, out_boxes, out_tags


def _detect_scale(boxes: Sequence[Any]) -> float:
    peak = 0.0
    for box in boxes:
        if not isinstance(box, Sequence) or isinstance(box, (str, bytes)):
            continue
        for value in list(box)[:4]:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number == number:  # not NaN
                peak = max(peak, abs(number))
    return 1.0 if peak <= 1.5 else float(LAYOUTLM_BBOX_SCALE)


def _clamp_int(value: float, scale: int) -> int:
    return max(0, min(scale, int(round(value))))
