from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

# Reuse normalization helpers to match training exactly
from .data_loader import _box_from_word, _normalize_box_from_extents


def _strip_bio(label: str) -> str:
    """Strip a leading ``B-``/``I-`` prefix from a label (``B-DATE`` -> ``DATE``)."""
    if label.startswith("B-") or label.startswith("I-"):
        return label[2:]
    return label


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
        # Base labels the model predicts, derived by stripping BIO prefixes
        # (e.g. {"ADDRESS", "AMOUNT", "DATE", ...}). normalize_label() needs
        # this because label_list itself is BIO-prefixed, so a base label like
        # "DATE" would otherwise never be recognized.
        self._base_label_set: Set[str] = {
            _strip_bio(lbl) for lbl in self._label_list if lbl != "O"
        }
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
        """Set of base labels the model predicts (excluding O, BIO stripped)."""
        return set(self._base_label_set)

    def normalize_label(self, raw_label: str) -> str:
        """Normalize a raw label to the model's base label set.

        A label is recognized if it is one of the model's base labels (after
        stripping any BIO prefix), or maps to one via the run.json merges.
        Returns 'O' for anything else.
        """
        base = _strip_bio(raw_label)

        # Base label the model directly predicts (e.g. DATE, MERCHANT_NAME).
        if base in self._base_label_set:
            return base

        # Fully-qualified label already in the model's list (defensive).
        if raw_label in self._label_list:
            return raw_label

        # Original label that was merged into a base label (e.g. ADDRESS_LINE
        # -> ADDRESS, LINE_TOTAL -> AMOUNT).
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
        tok = self._tokenizer
        model = self._model
        device = self._device
        torch = self._torch
        id2label = model.config.id2label
        id2label_items = list(id2label.items())

        n = len(tokens_per_line)
        results: List[Optional[LinePrediction]] = [None] * n
        if n == 0:
            return []

        # Each line is an independent LayoutLM sequence, but they were
        # previously run one forward at a time — for a ~40-line receipt that's
        # ~40 tiny GPU launches. Batch them (capped, so a huge receipt doesn't
        # blow up memory) into single padded forwards instead. Padded positions
        # are masked, so per-token outputs are identical to the per-line path.
        MAX_LINES_PER_BATCH = 64

        def _empty(idx: int) -> LinePrediction:
            return LinePrediction(
                line_id=(line_ids[idx] if line_ids else idx),
                tokens=tokens_per_line[idx],
                boxes=boxes_per_line[idx],
                labels=[],
                confidences=[],
                all_probabilities=[],
            )

        for start in range(0, n, MAX_LINES_PER_BATCH):
            end = min(start + MAX_LINES_PER_BATCH, n)
            # Lines with no tokens can't be tokenized; emit them as empty.
            members = [
                i for i in range(start, end) if len(tokens_per_line[i]) > 0
            ]
            for i in range(start, end):
                if len(tokens_per_line[i]) == 0:
                    results[i] = _empty(i)
            if not members:
                continue

            enc = tok(
                [tokens_per_line[i] for i in members],
                is_split_into_words=True,
                truncation=True,
                padding="longest",
                return_attention_mask=True,
            )
            bbox_batch: List[List[List[int]]] = []
            word_ids_per_member: List[List[Optional[int]]] = []
            for bi, line_idx in enumerate(members):
                wids = enc.word_ids(batch_index=bi)
                word_ids_per_member.append(wids)
                wb = boxes_per_line[line_idx]
                bbox_batch.append(
                    [[0, 0, 0, 0] if wid is None else wb[wid] for wid in wids]
                )

            inputs = {
                "input_ids": torch.tensor(enc["input_ids"], device=device),
                "attention_mask": torch.tensor(
                    enc["attention_mask"], device=device
                ),
                "bbox": torch.tensor(bbox_batch, device=device),
            }
            with torch.no_grad():
                logits = model(**inputs).logits  # [B, seq_len, num_labels]
            # Move raw logits to CPU once. The per-word aggregation below does
            # thousands of scalar reads; against a GPU tensor each forces a sync,
            # far slower than the matmul it follows. Aggregating logits (then
            # softmax per word) keeps this bit-equivalent to the per-line path.
            logits_batch = logits.cpu()
            softmax = torch.nn.functional.softmax

            for bi, line_idx in enumerate(members):
                wids = word_ids_per_member[bi]
                seq_logits = logits_batch[bi]  # [seq_len, num_labels]
                word_to_token_indices: Dict[int, List[int]] = {}
                for j, wid in enumerate(wids):
                    if wid is None:
                        continue
                    word_to_token_indices.setdefault(int(wid), []).append(j)

                word_boxes = boxes_per_line[line_idx]
                labels_per_word: List[str] = []
                confidences_per_word: List[float] = []
                all_probabilities_per_word: List[Dict[str, float]] = []
                for wid in range(len(word_boxes)):
                    token_idxs = word_to_token_indices.get(wid, [])
                    if not token_idxs:
                        labels_per_word.append("O")
                        confidences_per_word.append(0.0)
                        all_probabilities_per_word.append({})
                        continue
                    # Average logits across this word's subtokens, then softmax
                    # (matches the original per-line semantics exactly).
                    avg_logits = seq_logits[token_idxs].mean(dim=0)
                    probs = softmax(avg_logits, dim=-1)
                    conf, pred_id = torch.max(probs, dim=-1)
                    labels_per_word.append(id2label.get(int(pred_id.item()), "O"))
                    confidences_per_word.append(float(conf.item()))
                    probs_list = probs.tolist()
                    all_probabilities_per_word.append(
                        {name: probs_list[lid] for lid, name in id2label_items}
                    )

                results[line_idx] = LinePrediction(
                    line_id=(line_ids[line_idx] if line_ids else line_idx),
                    tokens=tokens_per_line[line_idx],
                    boxes=word_boxes,
                    labels=labels_per_word,
                    confidences=confidences_per_word,
                    all_probabilities=all_probabilities_per_word,
                )

        return [r if r is not None else _empty(i) for i, r in enumerate(results)]

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

    def predict_receipt_windowed(
        self,
        image_id: str,
        receipt_id: int,
        words: List[Any],
        window_size: Optional[int] = None,
        stride: Optional[int] = None,
    ) -> InferenceResult:
        """Run inference over sliding windows that mirror training.

        Training builds examples by sorting a receipt's words by ascending
        normalized (y, x) and cutting ``window_size``-word windows with
        ``stride`` step (see
        ``data_loader._build_receipt_window_examples``). The per-line path used
        elsewhere instead feeds one line at a time, so the model never sees the
        multi-line context it was trained on. This method reproduces the
        training windowing, predicts each window (batched), averages per-label
        probabilities for words that fall in overlapping windows, and returns
        the result as per-line ``LinePrediction``s so downstream code is
        unchanged.

        window_size/stride default to the same env vars training reads
        (LAYOUTLM_WINDOW_SIZE / LAYOUTLM_WINDOW_STRIDE), so setting them to the
        values a model trained with makes inference match it exactly.
        """
        if window_size is None:
            window_size = int(os.getenv("LAYOUTLM_WINDOW_SIZE", "250"))
        if stride is None:
            stride = int(os.getenv("LAYOUTLM_WINDOW_STRIDE", "200"))

        # Per-receipt extents → normalized boxes (matches training + per-line).
        max_x = max_y = 0.0
        raw: List[Tuple[int, int, str, Tuple[float, float, float, float]]] = []
        for w in words:
            x0, y0, x1, y1 = _box_from_word(w)
            max_x = max(max_x, x1)
            max_y = max(max_y, y1)
            raw.append((w.line_id, w.word_id, w.text, (x0, y0, x1, y1)))

        if not raw:
            return InferenceResult(
                image_id=image_id, receipt_id=receipt_id, lines=[], meta={}
            )

        items = [
            (
                lid,
                wid,
                text,
                _normalize_box_from_extents(
                    b[0], b[1], b[2], b[3], max_x, max_y
                ),
            )
            for (lid, wid, text, b) in raw
        ]

        # Order words by ascending normalized (y, x) — the EXACT key training
        # uses (data_loader._build_receipt_window_examples). NB: boxes come from
        # Apple Vision (origin bottom-left, y increases upward), so this is not
        # human top-to-bottom — but matching training is the whole point, so do
        # NOT "correct" it here without also changing the trainer.
        order = sorted(
            range(len(items)),
            key=lambda i: (items[i][3][1], items[i][3][0]),
        )
        ordered = [items[i] for i in order]
        tokens = [it[2] for it in ordered]
        boxes = [it[3] for it in ordered]
        n = len(ordered)

        starts = [0] if n <= window_size else list(range(0, n, stride))
        win_tokens = [tokens[s : min(s + window_size, n)] for s in starts]
        win_boxes = [boxes[s : min(s + window_size, n)] for s in starts]
        win_preds = self.predict_lines(win_tokens, win_boxes)

        # Average per-label probabilities across windows covering each word.
        prob_sums: List[Optional[Dict[str, float]]] = [None] * n
        prob_counts: List[int] = [0] * n
        for wi, s in enumerate(starts):
            for j, ap in enumerate(win_preds[wi].all_probabilities or []):
                oi = s + j
                if oi >= n:
                    break
                bucket = prob_sums[oi]
                if bucket is None:
                    bucket = {}
                    prob_sums[oi] = bucket
                for k, v in ap.items():
                    bucket[k] = bucket.get(k, 0.0) + v
                prob_counts[oi] += 1

        # Finalize each word, then regroup into per-line predictions ordered by
        # word_id (the order downstream token→word matching expects).
        by_line: Dict[int, List[Tuple[int, str, str, float, Dict[str, float]]]] = {}
        for oi in range(n):
            lid, wid, text, _box = ordered[oi]
            sums = prob_sums[oi]
            cnt = prob_counts[oi]
            if not sums or cnt == 0:
                by_line.setdefault(lid, []).append((wid, text, "O", 0.0, {}))
                continue
            probs = {k: v / cnt for k, v in sums.items()}
            label = max(probs, key=probs.get)
            by_line.setdefault(lid, []).append(
                (wid, text, label, probs[label], probs)
            )

        lines: List[LinePrediction] = []
        for lid in sorted(by_line):
            ws = sorted(by_line[lid], key=lambda t: t[0])
            lines.append(
                LinePrediction(
                    line_id=lid,
                    tokens=[t[1] for t in ws],
                    boxes=[],
                    labels=[t[2] for t in ws],
                    confidences=[t[3] for t in ws],
                    all_probabilities=[t[4] for t in ws],
                )
            )

        return InferenceResult(
            image_id=image_id,
            receipt_id=receipt_id,
            lines=lines,
            meta={
                "windowed": True,
                "window_size": window_size,
                "stride": stride,
                "num_windows": len(starts),
                "num_words": n,
            },
        )
