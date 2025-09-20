from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple

import os
import importlib


# Reuse normalization helpers to match training exactly
from .data_loader import _normalize_box_from_extents, _box_from_word


@dataclass
class LinePrediction:
    line_id: int
    tokens: List[str]
    boxes: List[List[int]]
    labels: List[str]
    confidences: List[float]


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
            from botocore.exceptions import ClientError as _S3ClientError  # type: ignore
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
            num_labels = seq_logits.shape[-1]

            # Collect token indices per word id
            word_to_token_indices: Dict[int, List[int]] = {}
            for i, wid in enumerate(wids):
                if wid is None:
                    continue
                word_to_token_indices.setdefault(int(wid), []).append(i)

            labels_per_word: List[str] = []
            confidences_per_word: List[float] = []
            for wid in range(len(word_boxes)):
                token_idxs = word_to_token_indices.get(wid, [])
                if not token_idxs:
                    # If tokenizer dropped the word (rare), fallback to zeros
                    labels_per_word.append("O")
                    confidences_per_word.append(0.0)
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

            results.append(
                LinePrediction(
                    line_id=(line_ids[idx] if line_ids else idx),
                    tokens=tokens,
                    boxes=word_boxes,
                    labels=labels_per_word,
                    confidences=confidences_per_word,
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
        by_line: Dict[int, List[Tuple[int, str, List[int]]]] = {}
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
