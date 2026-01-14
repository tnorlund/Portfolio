from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

# Reuse normalization helpers to match training exactly
from .config import FINANCIAL_REGION_LABELS
from .data_loader import _box_from_word, _normalize_box_from_extents


@dataclass
class LinePrediction:
    line_id: int
    tokens: List[str]
    boxes: List[List[int]]
    labels: List[str]
    confidences: List[float]
    # All class probabilities per word: List[Dict[label_name: str, probability: float]]
    all_probabilities: Optional[List[Dict[str, float]]] = None


@dataclass
class InferenceResult:
    image_id: str
    receipt_id: int
    lines: List[LinePrediction]
    meta: Dict[str, Any]


class LayoutLMInference:
    """Lightweight, dependency-tolerant inference wrapper for LayoutLM.

    This class loads a tokenizer and model either from a local directory
    or from an S3 URI (s3://bucket/prefix/). Box normalization logic matches
    training utilities to ensure coordinate consistency.
    """

    def __init__(
        self,
        model_dir: Optional[str] = None,
        model_s3_uri: Optional[str] = None,
        auto_from_bucket_env: Optional[str] = None,
    ) -> None:
        """Initialize inference.

        Args:
            model_dir: Local directory containing tokenizer and model files.
            model_s3_uri: If provided, sync this S3 prefix to model_dir.
            auto_from_bucket_env: If provided (env var name), attempts to
                discover latest run under s3://$ENV/runs/ and prefer best/.
        """
        self._transformers = importlib.import_module("transformers")
        self._torch = importlib.import_module("torch")

        resolved_dir = model_dir or os.path.expanduser("~/model")
        os.makedirs(resolved_dir, exist_ok=True)

        if not model_s3_uri and auto_from_bucket_env:
            model_s3_uri = self._resolve_latest_s3_uri(auto_from_bucket_env)

        if model_s3_uri:
            self._sync_from_s3(model_s3_uri, resolved_dir)
        # record where model came from
        self._model_dir = resolved_dir
        self._s3_uri_used = model_s3_uri

        # Load tokenizer and model from directory
        self._tokenizer = (
            self._transformers.LayoutLMTokenizerFast.from_pretrained(
                resolved_dir
            )
        )
        self._model = (
            self._transformers.LayoutLMForTokenClassification.from_pretrained(
                resolved_dir
            ).eval()
        )
        self._device = "cuda" if self._torch.cuda.is_available() else "cpu"
        self._model.to(self._device)

        # Load label configuration from run.json if available
        self._label_list: List[str] = list(
            self._model.config.id2label.values()
        )
        self._label_merges: Dict[str, List[str]] = {}
        self._reverse_label_map: Dict[str, str] = {}
        self._load_run_config(model_s3_uri)

    # ----------------------- Label configuration -----------------------
    def _load_run_config(self, s3_uri: Optional[str]) -> None:
        """Load run.json from S3 to get label_merges configuration.

        The run.json is stored at the run level (parent of best/ or checkpoint/).
        """
        # First try local model_dir
        local_run_json = os.path.join(self._model_dir, "run.json")
        if os.path.exists(local_run_json):
            self._parse_run_json(local_run_json)
            return

        if not s3_uri or not s3_uri.startswith("s3://"):
            return

        try:
            boto3 = importlib.import_module("boto3")
            from urllib.parse import urlparse
        except ModuleNotFoundError:
            return

        # run.json is at the run level, not in best/ or checkpoint-*/
        # e.g., s3://bucket/runs/layoutlm-8-labels/run.json
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")

        # Go up one level from best/ or checkpoint-* to find run.json
        if prefix.endswith("/"):
            prefix = prefix[:-1]
        parent_prefix = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        run_json_key = (
            f"{parent_prefix}/run.json" if parent_prefix else "run.json"
        )

        s3 = boto3.client("s3")
        try:
            # Download run.json to local model_dir
            s3.download_file(bucket, run_json_key, local_run_json)
            self._parse_run_json(local_run_json)
        except Exception as e:
            # run.json not found or other error - continue without it
            import logging

            logging.getLogger(__name__).debug(
                "Could not load run.json from S3: %s", e
            )

    def _parse_run_json(self, path: str) -> None:
        """Parse run.json and build reverse label mapping."""
        with open(path, "r") as f:
            config = json.load(f)

        self._label_merges = config.get("label_merges", {})

        # Build reverse mapping: original_label -> merged_label
        for merged_label, original_labels in self._label_merges.items():
            for orig in original_labels:
                self._reverse_label_map[orig] = merged_label

    @property
    def label_list(self) -> List[str]:
        """List of labels the model was trained on."""
        return self._label_list

    @property
    def label_merges(self) -> Dict[str, List[str]]:
        """Label merge configuration from run.json."""
        return self._label_merges

    @property
    def base_labels(self) -> Set[str]:
        """Set of base labels (excluding O)."""
        return {lbl for lbl in self._label_list if lbl != "O"}

    def normalize_label(self, raw_label: str) -> str:
        """Normalize a raw label to match the model's label set.

        Uses label_merges from run.json to map original labels to merged ones.
        Returns 'O' for unknown labels.
        """
        # Already a valid label
        if raw_label in self._label_list:
            return raw_label

        # Check if it's a label that was merged
        if raw_label in self._reverse_label_map:
            return self._reverse_label_map[raw_label]

        # Unknown label
        return "O"

    # ----------------------- S3 utilities -----------------------
    def _resolve_latest_s3_uri(self, bucket_env_var: str) -> Optional[str]:
        """Resolve latest run URI from an S3 bucket env var.

        Looks for s3://$BUCKET/runs/<job>/ and prefers a best/ subdir if present.
        Returns an s3 URI ending with a trailing '/'.
        """
        bucket = os.getenv(bucket_env_var)
        if not bucket:
            return None
        try:
            boto3 = importlib.import_module("boto3")
        except ModuleNotFoundError:
            return None

        s3 = boto3.client("s3")
        resp = s3.list_objects_v2(Bucket=bucket, Prefix="runs/", Delimiter="/")
        prefixes = [cp["Prefix"] for cp in resp.get("CommonPrefixes", [])]
        if not prefixes:
            return None

        latest_prefix, latest_ts = None, None
        for p in prefixes:
            page = s3.list_objects_v2(Bucket=bucket, Prefix=p)
            contents = page.get("Contents", [])
            if not contents:
                continue
            ts = max(obj["LastModified"] for obj in contents)
            if latest_ts is None or ts > latest_ts:
                latest_ts, latest_prefix = ts, p
        if not latest_prefix:
            return None

        # Prefer best/ if present
        try:
            from botocore.exceptions import (
                ClientError as _S3ClientError,  # type: ignore
            )
        except (
            ModuleNotFoundError
        ):  # pragma: no cover - when botocore not installed
            _S3ClientError = None  # type: ignore

        try:
            s3.head_object(
                Bucket=bucket, Key=f"{latest_prefix}best/model.safetensors"
            )
            return f"s3://{bucket}/{latest_prefix}best/"
        except _S3ClientError as _:
            # Fallback: pick latest checkpoint containing model.safetensors
            page = s3.list_objects_v2(Bucket=bucket, Prefix=latest_prefix)
            cand = [
                o
                for o in page.get("Contents", [])
                if o["Key"].endswith("model.safetensors")
                and "checkpoint-" in o["Key"]
            ]
            if not cand:
                return None
            latest = max(cand, key=lambda o: o["LastModified"])
            return "s3://{}/{}".format(
                bucket, latest["Key"].rsplit("/", 1)[0] + "/"
            )

    def _sync_from_s3(self, s3_uri: str, dst_dir: str) -> None:
        """Download all objects under an s3://bucket/prefix/ into dst_dir.

        Uses boto3 if available; otherwise falls back to AWS CLI if present.
        """
        if not s3_uri.startswith("s3://"):
            return
        try:
            boto3 = importlib.import_module("boto3")
            from urllib.parse import urlparse

            parsed = urlparse(s3_uri)
            bucket = parsed.netloc
            prefix = parsed.path.lstrip("/")
            s3 = boto3.client("s3")
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []) or []:
                    key = obj["Key"]
                    rel = key[len(prefix) :]
                    if not rel:
                        continue
                    local_path = os.path.join(dst_dir, rel)
                    os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    s3.download_file(bucket, key, local_path)
            return
        except ModuleNotFoundError:
            pass
        # Fallback to AWS CLI
        if (
            os.system(
                f"aws s3 sync {s3_uri} {dst_dir} --no-progress > /dev/null 2>&1"
            )
            != 0
        ):
            # Best-effort; ignore failures to keep CPU-only usage possible
            return

    # ----------------------- Public API -----------------------
    def predict_lines(
        self,
        tokens_per_line: List[List[str]],
        boxes_per_line: List[List[List[int]]],
        line_ids: Optional[List[int]] = None,
    ) -> List[LinePrediction]:
        """Run prediction for provided lines (already normalized boxes).

        boxes must be LayoutLM-style per-subtoken bboxes after alignment or
        per-word boxes (we align internally to subtokens by repeating word boxes).
        """
        results: List[LinePrediction] = []
        tok = self._tokenizer
        model = self._model
        device = self._device

        for idx, (tokens, word_boxes) in enumerate(
            zip(tokens_per_line, boxes_per_line)
        ):
            enc = tok(
                tokens,
                is_split_into_words=True,
                truncation=True,
                padding="longest",
                return_attention_mask=True,
            )
            wids = enc.word_ids()
            bbox_aligned = [
                [0, 0, 0, 0] if wid is None else word_boxes[wid]
                for wid in wids
            ]

            inputs = {
                "input_ids": self._torch.tensor(
                    [enc["input_ids"]], device=device
                ),
                "attention_mask": self._torch.tensor(
                    [enc["attention_mask"]], device=device
                ),
                "bbox": self._torch.tensor([bbox_aligned], device=device),
            }
            with self._torch.no_grad():
                logits = model(**inputs).logits  # [1, seq_len, num_labels]
            id2label = model.config.id2label

            # Aggregate logits per word (average over subtokens), compute softmax
            seq_logits = logits[0]  # [seq_len, num_labels]

            # Collect token indices per word id
            word_to_token_indices: Dict[int, List[int]] = {}
            for i, wid in enumerate(wids):
                if wid is None:
                    continue
                word_to_token_indices.setdefault(int(wid), []).append(i)

            labels_per_word: List[str] = []
            confidences_per_word: List[float] = []
            all_probabilities_per_word: List[Dict[str, float]] = []
            for wid in range(len(word_boxes)):
                token_idxs = word_to_token_indices.get(wid, [])
                if not token_idxs:
                    # If tokenizer dropped the word (rare), fallback to zeros
                    labels_per_word.append("O")
                    confidences_per_word.append(0.0)
                    all_probabilities_per_word.append({})
                    continue
                # Average logits across subtokens of this word
                stacked = self._torch.stack(
                    [seq_logits[i] for i in token_idxs]
                )
                avg_logits = stacked.mean(dim=0)
                probs = self._torch.nn.functional.softmax(avg_logits, dim=-1)
                conf, pred_id = self._torch.max(probs, dim=-1)
                labels_per_word.append(id2label.get(int(pred_id.item()), "O"))
                confidences_per_word.append(float(conf.item()))

                # Store all class probabilities
                word_probs: Dict[str, float] = {}
                for label_id, label_name in id2label.items():
                    word_probs[label_name] = float(probs[label_id].item())
                all_probabilities_per_word.append(word_probs)

            results.append(
                LinePrediction(
                    line_id=(line_ids[idx] if line_ids else idx),
                    tokens=tokens,
                    boxes=word_boxes,
                    labels=labels_per_word,
                    confidences=confidences_per_word,
                    all_probabilities=all_probabilities_per_word,
                )
            )

        return results

    def predict_receipt_from_dynamo(
        self,
        dynamo_client: Any,
        image_id: str,
        receipt_id: int,
    ) -> InferenceResult:
        """Fetch a receipt via DynamoClient and run inference per line.

        Normalizes boxes using per-receipt extents to match training.
        """
        details = dynamo_client.get_receipt_details(image_id, receipt_id)
        words = details.words

        # Group words by line and compute extents
        # Store raw float coordinates; normalization handles rounding
        by_line: Dict[int, List[Tuple[int, str, List[float]]]] = {}
        max_x = 0.0
        max_y = 0.0
        for w in words:
            x0, y0, x1, y1 = _box_from_word(w)
            max_x = max(max_x, x1)
            max_y = max(max_y, y1)
            by_line.setdefault(w.line_id, []).append(
                (w.word_id, w.text, [x0, y0, x1, y1])
            )

        tokens_per_line: List[List[str]] = []
        boxes_per_line: List[List[List[int]]] = []
        line_ids: List[int] = []
        for line_id, items in sorted(by_line.items()):
            items.sort(key=lambda t: t[0])
            tokens = [tok for _, tok, _ in items]
            raw_boxes = [box for _, _, box in items]
            nboxes = [
                _normalize_box_from_extents(
                    b[0], b[1], b[2], b[3], max_x, max_y
                )
                for b in raw_boxes
            ]
            tokens_per_line.append(tokens)
            boxes_per_line.append(nboxes)
            line_ids.append(line_id)

        line_preds = self.predict_lines(
            tokens_per_line=tokens_per_line,
            boxes_per_line=boxes_per_line,
            line_ids=line_ids,
        )

        return InferenceResult(
            image_id=image_id,
            receipt_id=receipt_id,
            lines=line_preds,
            meta={
                "num_lines": len(line_preds),
                "num_words": sum(len(lp.tokens) for lp in line_preds),
                "device": self._device,
                "model_name": getattr(
                    self._model.config, "_name_or_path", None
                ),
                "s3_uri_used": self._s3_uri_used,
                "model_dir": self._model_dir,
            },
        )


@dataclass
class FinancialRegion:
    """Detected financial region from Pass 1 inference."""

    y_min: float  # Minimum Y coordinate (normalized 0-1000)
    y_max: float  # Maximum Y coordinate (normalized 0-1000)
    num_tokens: int  # Number of tokens in the region
    avg_confidence: float  # Average confidence of FINANCIAL_REGION predictions
    line_ids: List[int]  # Line IDs that overlap with the region


class TwoPassLayoutLMInference:
    """Two-pass hierarchical inference for LayoutLM models.

    Pass 1: Detect coarse regions (e.g., FINANCIAL_REGION)
    Pass 2: Within detected regions, classify fine-grained labels

    This approach improves classification of visually similar labels
    (LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL) by first isolating the
    financial region, then running a specialized model on just that region.
    """

    def __init__(
        self,
        pass1_model_dir: Optional[str] = None,
        pass1_model_s3_uri: Optional[str] = None,
        pass2_model_dir: Optional[str] = None,
        pass2_model_s3_uri: Optional[str] = None,
        min_region_tokens: int = 3,
        min_confidence_skip_pass2: float = 0.95,
    ) -> None:
        """Initialize two-pass inference.

        Args:
            pass1_model_dir: Local directory for Pass 1 model.
            pass1_model_s3_uri: S3 URI for Pass 1 model (coarse detection).
            pass2_model_dir: Local directory for Pass 2 model.
            pass2_model_s3_uri: S3 URI for Pass 2 model (fine-grained classification).
            min_region_tokens: Minimum tokens in region to run Pass 2 (default: 3).
            min_confidence_skip_pass2: Skip Pass 2 if avg confidence > this (default: 0.95).
        """
        # Initialize both models
        self._pass1 = LayoutLMInference(
            model_dir=pass1_model_dir or os.path.expanduser("~/model_pass1"),
            model_s3_uri=pass1_model_s3_uri,
        )
        self._pass2 = LayoutLMInference(
            model_dir=pass2_model_dir or os.path.expanduser("~/model_pass2"),
            model_s3_uri=pass2_model_s3_uri,
        )

        self._min_region_tokens = min_region_tokens
        self._min_confidence_skip_pass2 = min_confidence_skip_pass2

        # Financial region label (expected from Pass 1 with two_pass_p1 merge preset)
        self._financial_region_label = "FINANCIAL_REGION"

    def extract_financial_region(
        self,
        pass1_result: InferenceResult,
    ) -> Optional[FinancialRegion]:
        """Extract the financial region Y-range from Pass 1 predictions.

        Finds all tokens predicted as FINANCIAL_REGION (or B-FINANCIAL_REGION,
        I-FINANCIAL_REGION) and computes their Y-range.

        Args:
            pass1_result: Result from Pass 1 inference.

        Returns:
            FinancialRegion if detected, None otherwise.
        """
        region_y_coords: List[float] = []
        region_confidences: List[float] = []
        region_line_ids: Set[int] = set()

        for line in pass1_result.lines:
            for i, (label, confidence, box) in enumerate(
                zip(line.labels, line.confidences, line.boxes)
            ):
                # Check for FINANCIAL_REGION label (with or without BIO prefix)
                base_label = label.replace("B-", "").replace("I-", "")
                if base_label == self._financial_region_label:
                    # box is [x0, y0, x1, y1] normalized to 0-1000
                    region_y_coords.extend([box[1], box[3]])
                    region_confidences.append(confidence)
                    region_line_ids.add(line.line_id)

        if not region_y_coords:
            return None

        return FinancialRegion(
            y_min=min(region_y_coords),
            y_max=max(region_y_coords),
            num_tokens=len(region_confidences),
            avg_confidence=sum(region_confidences) / len(region_confidences),
            line_ids=sorted(region_line_ids),
        )

    def should_run_pass2(
        self,
        region: Optional[FinancialRegion],
    ) -> bool:
        """Determine whether to run Pass 2 on a detected region.

        Skip Pass 2 if:
        - No financial region detected
        - Region is too small (< min_region_tokens)
        - Pass 1 confidence is very high (> min_confidence_skip_pass2)

        Args:
            region: Detected financial region from Pass 1, or None.

        Returns:
            True if Pass 2 should be run, False otherwise.
        """
        if region is None:
            return False

        if region.num_tokens < self._min_region_tokens:
            return False

        # Note: We intentionally don't skip based on high confidence
        # because Pass 1 predicts FINANCIAL_REGION, not the fine-grained labels.
        # High confidence in Pass 1 just means we're confident it's *a* financial
        # region, not that we know *which* specific label it is.

        return True

    def filter_lines_to_region(
        self,
        tokens_per_line: List[List[str]],
        boxes_per_line: List[List[List[int]]],
        line_ids: List[int],
        region: FinancialRegion,
        y_margin: float = 50.0,  # Normalized margin (out of 1000)
    ) -> Tuple[List[List[str]], List[List[List[int]]], List[int], List[int]]:
        """Filter lines to only those within the financial region Y-range.

        Args:
            tokens_per_line: Tokens for each line.
            boxes_per_line: Normalized boxes for each line.
            line_ids: Original line IDs.
            region: Detected financial region.
            y_margin: Additional margin around region (default: 50 normalized units).

        Returns:
            Tuple of (filtered_tokens, filtered_boxes, filtered_line_ids, original_indices).
            original_indices maps each filtered line back to its position in the input.
        """
        y_min = region.y_min - y_margin
        y_max = region.y_max + y_margin

        filtered_tokens: List[List[str]] = []
        filtered_boxes: List[List[List[int]]] = []
        filtered_line_ids: List[int] = []
        original_indices: List[int] = []

        for idx, (tokens, boxes, line_id) in enumerate(
            zip(tokens_per_line, boxes_per_line, line_ids)
        ):
            # Check if any word in this line overlaps with the Y-range
            line_overlaps = False
            for box in boxes:
                word_y_min, word_y_max = box[1], box[3]
                if word_y_max >= y_min and word_y_min <= y_max:
                    line_overlaps = True
                    break

            if line_overlaps:
                filtered_tokens.append(tokens)
                filtered_boxes.append(boxes)
                filtered_line_ids.append(line_id)
                original_indices.append(idx)

        return filtered_tokens, filtered_boxes, filtered_line_ids, original_indices

    def merge_predictions(
        self,
        pass1_result: InferenceResult,
        pass2_predictions: List[LinePrediction],
        region: FinancialRegion,
    ) -> InferenceResult:
        """Merge Pass 1 and Pass 2 predictions.

        For lines within the financial region, use Pass 2 predictions.
        For lines outside the region, use Pass 1 predictions.

        Args:
            pass1_result: Full receipt predictions from Pass 1.
            pass2_predictions: Fine-grained predictions for financial region lines.
            region: The detected financial region.

        Returns:
            Merged InferenceResult with fine-grained labels in financial region.
        """
        # Build lookup for Pass 2 predictions by line_id
        pass2_by_line: Dict[int, LinePrediction] = {
            pred.line_id: pred for pred in pass2_predictions
        }

        merged_lines: List[LinePrediction] = []
        for line in pass1_result.lines:
            if line.line_id in pass2_by_line:
                # Use Pass 2 predictions for this line
                merged_lines.append(pass2_by_line[line.line_id])
            else:
                # Keep Pass 1 predictions
                merged_lines.append(line)

        return InferenceResult(
            image_id=pass1_result.image_id,
            receipt_id=pass1_result.receipt_id,
            lines=merged_lines,
            meta={
                **pass1_result.meta,
                "two_pass": True,
                "financial_region": {
                    "y_min": region.y_min,
                    "y_max": region.y_max,
                    "num_tokens": region.num_tokens,
                    "avg_confidence": region.avg_confidence,
                    "num_lines": len(region.line_ids),
                },
                "pass2_lines": len(pass2_predictions),
            },
        )

    def predict_receipt_from_dynamo(
        self,
        dynamo_client: Any,
        image_id: str,
        receipt_id: int,
    ) -> InferenceResult:
        """Run two-pass inference on a receipt from DynamoDB.

        1. Run Pass 1 on full receipt to detect FINANCIAL_REGION
        2. Extract Y-range of detected region
        3. If region found, run Pass 2 on just that region
        4. Merge results

        Args:
            dynamo_client: DynamoDB client instance.
            image_id: Receipt image ID.
            receipt_id: Receipt ID.

        Returns:
            InferenceResult with merged predictions.
        """
        # Step 1: Run Pass 1 on full receipt
        pass1_result = self._pass1.predict_receipt_from_dynamo(
            dynamo_client, image_id, receipt_id
        )

        # Step 2: Extract financial region
        region = self.extract_financial_region(pass1_result)

        # Step 3: Check if Pass 2 is needed
        if not self.should_run_pass2(region):
            # Return Pass 1 results with metadata indicating no Pass 2
            pass1_result.meta["two_pass"] = False
            pass1_result.meta["pass2_skipped_reason"] = (
                "no_region" if region is None else "region_too_small"
            )
            return pass1_result

        # Step 4: Prepare data for Pass 2 (filter to region)
        # We need to rebuild tokens/boxes from pass1_result
        tokens_per_line = [line.tokens for line in pass1_result.lines]
        boxes_per_line = [line.boxes for line in pass1_result.lines]
        line_ids = [line.line_id for line in pass1_result.lines]

        filtered_tokens, filtered_boxes, filtered_line_ids, _ = (
            self.filter_lines_to_region(
                tokens_per_line, boxes_per_line, line_ids, region
            )
        )

        # Step 5: Run Pass 2 on filtered region
        pass2_predictions = self._pass2.predict_lines(
            tokens_per_line=filtered_tokens,
            boxes_per_line=filtered_boxes,
            line_ids=filtered_line_ids,
        )

        # Step 6: Merge results
        return self.merge_predictions(pass1_result, pass2_predictions, region)
