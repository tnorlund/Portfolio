"""
LLM-assisted receipt combination selector.

Given a target receipt that lacks metadata, this helper builds candidate
combinations with other receipts from the same image, renders their text in
image order (top-to-bottom, left-to-right), and asks an LLM to choose the best
combination or NONE.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from langchain_ollama import ChatOllama

# Optional LangSmith tracing (graceful if not installed)
try:
    from langsmith.run_tree import RunTree
except ImportError:  # pragma: no cover - optional dependency
    RunTree = None  # type: ignore

from receipt_agent.config.settings import get_settings
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env, load_secrets
from receipt_dynamo.entities import Image, Receipt, ReceiptLine


def _warp_point(
    coeffs: Sequence[float], x: float, y: float
) -> Tuple[float, float]:
    """
    Apply perspective transform (receipt space -> image space).
    coeffs: [a, b, c, d, e, f, g, h]
    """
    a, b, c, d, e, f, g, h = coeffs
    den = g * x + h * y + 1.0
    if abs(den) < 1e-12:
        return (x, y)
    return ((a * x + b * y + c) / den, (d * x + e * y + f) / den)


def _line_centroid_image(
    receipt: Receipt, line: ReceiptLine, image_width: int, image_height: int
) -> Tuple[float, float]:
    """Compute line centroid in image space using the receipt transform."""
    coeffs, receipt_w, receipt_h = receipt.get_transform_to_image(
        image_width, image_height
    )
    # Lines store corners normalized to receipt space, y in OCR space (bottom=0).
    pts = [
        line.top_left,
        line.top_right,
        line.bottom_right,
        line.bottom_left,
    ]
    px_pts = []
    for pt in pts:
        rx = pt["x"] * receipt_w
        ry = pt["y"] * receipt_h
        x_img, y_img = _warp_point(coeffs, rx, ry)
        px_pts.append((x_img, y_img))
    cx = sum(p[0] for p in px_pts) / 4.0
    cy = sum(p[1] for p in px_pts) / 4.0
    return cx, cy


def _sort_lines_ocr_space(lines: Iterable[ReceiptLine]) -> List[ReceiptLine]:
    """
    Sort lines in OCR space (y=0 at bottom) without warping to image space.
    Top-to-bottom: larger y first, then left-to-right: smaller x first.
    """
    with_keys = []
    for ln in lines:
        # Use top_left corner (already normalized 0-1 in OCR space)
        y = ln.top_left["y"]
        x = ln.top_left["x"]
        with_keys.append((-y, x, ln))  # negate y to sort descending by y
    with_keys.sort(key=lambda t: (t[0], t[1]))
    return [ln for _, _, ln in with_keys]


def _sort_lines_image_space(
    receipt: Receipt,
    lines: Iterable[ReceiptLine],
    image_width: int,
    image_height: int,
) -> List[ReceiptLine]:
    """Sort lines top-to-bottom, left-to-right using image-space centroids."""
    with_centroids = []
    for ln in lines:
        cx, cy = _line_centroid_image(receipt, ln, image_width, image_height)
        with_centroids.append((cy, cx, ln))
    # Top to bottom (smaller y first), then left to right (smaller x)
    with_centroids.sort(key=lambda t: (t[0], t[1]))
    return [ln for _, _, ln in with_centroids]


def _format_candidate_text(
    receipt: Receipt,
    lines: Sequence[ReceiptLine],
    image_width: int,
    image_height: int,
) -> str:
    """Render receipt lines in reading order with image-space sorting."""
    sorted_lines = _sort_lines_image_space(
        receipt, lines, image_width, image_height
    )
    parts = []
    for ln in sorted_lines:
        parts.append(f"- {ln.text}")
    return "\n".join(parts)


def _format_combined_text(
    receipt_a: Receipt,
    lines_a: Sequence[ReceiptLine],
    receipt_b: Receipt,
    lines_b: Sequence[ReceiptLine],
    image_width: int,
    image_height: int,
) -> str:
    """
    Render both receipts together in global order using OCR space only.
    No warp; just OCR y (top has larger y), then x ascending.
    """
    merged: List[Tuple[float, float, ReceiptLine]] = []
    for ln in lines_a:
        merged.append((-ln.top_left["y"], ln.top_left["x"], ln))
    for ln in lines_b:
        merged.append((-ln.top_left["y"], ln.top_left["x"], ln))
    merged.sort(key=lambda t: (t[0], t[1]))
    return "\n".join(f"- {ln.text}" for _, _, ln in merged)


class ReceiptCombinationSelector:
    """
    Build candidate combinations and ask an LLM to choose the best one.
    """

    def __init__(
        self, dynamo: DynamoClient, llm_client: Any | None = None
    ) -> None:
        self.dynamo = dynamo
        # Hydrate env from Pulumi if available (local convenience)
        try:
            stack = os.environ.get("PULUMI_STACK", "dev")
            env = load_env(stack, working_dir="infra")
            secrets = load_secrets(stack, working_dir="infra")
            table = env.get("dynamodb_table_name") or env.get(
                "receipts_table_name"
            )
            if table:
                os.environ.setdefault("DYNAMODB_TABLE_NAME", table)
            ollama_key = secrets.get("OLLAMA_API_KEY") or secrets.get(
                "portfolio:OLLAMA_API_KEY"
            )
            if ollama_key:
                os.environ.setdefault(
                    "RECEIPT_AGENT_OLLAMA_API_KEY", ollama_key
                )
            langchain_key = secrets.get("LANGCHAIN_API_KEY") or secrets.get(
                "portfolio:LANGCHAIN_API_KEY"
            )
            if langchain_key:
                os.environ.setdefault("LANGCHAIN_API_KEY", langchain_key)
        except Exception:
            pass

        # Use provided client or default Ollama client.
        settings = get_settings()
        if llm_client:
            self.llm = llm_client
        else:
            api_key = settings.ollama_api_key.get_secret_value() or ""
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            self.llm = ChatOllama(
                base_url=settings.ollama_base_url,
                model=settings.ollama_model,
                temperature=0,
                client_kwargs={
                    "headers": headers,
                    "timeout": 120,
                },
            )

    def build_candidates(
        self, image_id: str, target_receipt_id: int
    ) -> List[Dict[str, Any]]:
        """Create pairwise candidates: (target, other) for all other receipts."""
        image = self.dynamo.get_image(image_id)
        receipts = self.dynamo.get_receipts_from_image(image_id)
        target = next(
            (r for r in receipts if r.receipt_id == target_receipt_id), None
        )
        if not target:
            raise ValueError(
                f"Target receipt {target_receipt_id} not found for {image_id}"
            )
        others = [r for r in receipts if r.receipt_id != target_receipt_id]
        if not others:
            return []
        image_width = image.width
        image_height = image.height
        candidates = []
        for other in others:
            lines_target = self.dynamo.list_receipt_lines_from_receipt(
                image_id, target.receipt_id
            )
            lines_other = self.dynamo.list_receipt_lines_from_receipt(
                image_id, other.receipt_id
            )
            candidates.append(
                {
                    "combo": [target.receipt_id, other.receipt_id],
                    "text_target": _format_candidate_text(
                        target, lines_target, image_width, image_height
                    ),
                    "text_other": _format_candidate_text(
                        other, lines_other, image_width, image_height
                    ),
                    "text_combined": _format_combined_text(
                        target,
                        lines_target,
                        other,
                        lines_other,
                        image_width,
                        image_height,
                    ),
                }
            )
        return candidates

    def _build_prompt(self, candidates: Sequence[Dict[str, Any]]) -> str:
        """Build a JSON-only prompt."""
        lines = [
            "Decide if two receipts belong to the SAME transaction.",
            "Rules to accept a match:",
            "- Same merchant/store OR clearly identical location/contact info.",
            "- Same or near-identical date/time (same day; slight time drift ok).",
            "- Totals, taxes, card tails should be consistent (not conflicting).",
            "- Item lines should be plausibly the same purchase; no conflicting item sets.",
            "If there is any strong conflict (different merchant names, different dates, very different totals/card tails), return null.",
            "",
            'Respond ONLY with JSON: {"choice": <int or null>, "reason": "..."}',
            "Use choice=null if none fit.",
            "",
        ]
        for idx, cand in enumerate(candidates, start=1):
            a, b = cand["combo"]
            lines.append(f"OPTION {idx}: receipt {a} + receipt {b}")
            lines.append("Receipt A lines:")
            lines.append(cand["text_target"])
            lines.append("Receipt B lines:")
            lines.append(cand["text_other"])
            if cand.get("text_combined"):
                lines.append("Combined lines (image order):")
                lines.append(cand["text_combined"])
            lines.append("")
        lines.append(
            'Return JSON only. Example: {"choice": 2, "reason": "..."} or {"choice": null, "reason": "..."}'
        )
        return "\n".join(lines)

    def choose(self, image_id: str, target_receipt_id: int) -> Dict[str, Any]:
        """Return the chosen combination or NONE."""
        candidates = self.build_candidates(image_id, target_receipt_id)
        if not candidates:
            return {"status": "no_candidates", "choice": None}

        # Prepare minimal, privacy-aware trace payload (no full text)
        candidate_pairs = [c["combo"] for c in candidates]
        prompt = self._build_prompt(candidates)
        prompt_len = len(prompt)

        run: RunTree | None = None
        if RunTree is not None:
            try:
                run = RunTree(
                    name="receipt-combination-llm",
                    run_type="llm",
                    inputs={
                        "image_id": image_id,
                        "target_receipt_id": target_receipt_id,
                        "candidate_pairs": candidate_pairs,
                        "prompt_length": prompt_len,
                    },
                    tags=[
                        "combine-receipts",
                        f"image:{image_id}",
                        os.environ.get("PULUMI_STACK", "dev"),
                    ],
                    metadata={
                        "model": getattr(self.llm, "model", None),
                        "base_url": getattr(self.llm, "base_url", None),
                    },
                )
            except Exception:
                run = None

        prompt = self._build_prompt(candidates)
        result = self.llm.invoke(prompt)
        raw_answer = (
            result.content if hasattr(result, "content") else str(result)
        )

        choice = None
        try:
            parsed = json.loads(raw_answer)
            pick = parsed.get("choice")
            if pick is None:
                choice = None
            elif isinstance(pick, int) and 1 <= pick <= len(candidates):
                choice = candidates[pick - 1]["combo"]
        except Exception:
            choice = None

        outputs = {
            "status": "ok",
            "choice": choice,
            "raw_answer": raw_answer,
            "candidates": candidate_pairs,
        }
        if run is not None:
            try:
                run.end(
                    outputs={
                        "choice": choice,
                        "raw_answer_excerpt": raw_answer[:2000],
                        "status": outputs["status"],
                        "candidate_pairs": candidate_pairs,
                        "prompt_length": prompt_len,
                    }
                )
            except Exception:
                pass

        return outputs
