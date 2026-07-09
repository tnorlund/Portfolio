import importlib
import inspect
import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from functools import partial
from glob import glob
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import CoreMLExportStatus
from receipt_dynamo.entities.coreml_export_job import CoreMLExportJob
from receipt_dynamo.entities.job import Job
from receipt_dynamo.entities.job_log import JobLog
from receipt_dynamo.entities.job_metric import JobMetric

from .config import DataConfig, TrainingConfig
from .data_loader import MergeInfo, load_datasets


def _checkpoint_step(path: str) -> int:
    """Extract the numeric step from a checkpoint path for proper sorting.

    ``checkpoint-950`` and ``checkpoint-24354`` sort incorrectly when compared
    lexicographically.  This helper pulls the integer so callers can sort
    numerically.
    """
    m = re.search(r"checkpoint-(\d+)", path)
    return int(m.group(1)) if m else 0


_CHECKPOINT_METRIC_ALIASES = {
    "f1": "eval_f1",
    "eval_f1": "eval_f1",
    "product_detail_macro_f1": "eval_product_detail_macro_f1",
    "eval_product_detail_macro_f1": "eval_product_detail_macro_f1",
    "loss": "eval_loss",
    "eval_loss": "eval_loss",
    "accuracy": "eval_accuracy",
    "eval_accuracy": "eval_accuracy",
}
_SEQEVAL_FREE_CHECKPOINT_METRICS = {"eval_accuracy", "eval_loss"}


def _checkpoint_metric_for_trainer(metric: str | None) -> str:
    """Normalize a configured metric to HuggingFace Trainer's eval_* key."""
    raw = (metric or "f1").strip()
    if raw in _CHECKPOINT_METRIC_ALIASES:
        return _CHECKPOINT_METRIC_ALIASES[raw]
    if raw.startswith("eval_"):
        return raw
    return f"eval_{raw}"


def _checkpoint_metric_for_available_metrics(
    metric: str | None, *, seqeval_available: bool
) -> str:
    checkpoint_metric = _checkpoint_metric_for_trainer(metric)
    if (
        not seqeval_available
        and checkpoint_metric not in _SEQEVAL_FREE_CHECKPOINT_METRICS
    ):
        return "eval_accuracy"
    return checkpoint_metric


def _metric_greater_is_better(metric: str) -> bool:
    """Return whether higher values should win for checkpoint selection."""
    return not metric.endswith("loss")


def _epoch_metric_values(
    epoch_metrics: List[Dict[str, Any]],
    metric: str,
) -> List[Tuple[float, float]]:
    """Extract (epoch, value) pairs for a metric from run.json entries."""
    candidates = [metric]
    if metric.startswith("eval_"):
        candidates.append(metric.removeprefix("eval_"))
    else:
        candidates.append(f"eval_{metric}")

    values: List[Tuple[float, float]] = []
    for entry in epoch_metrics:
        epoch = entry.get("epoch")
        if epoch is None:
            continue
        for key in candidates:
            value = entry.get(key)
            if isinstance(value, (int, float)):
                values.append((float(epoch), float(value)))
                break
    return values


SAGEMAKER_CHECKPOINT_DIR = "/opt/ml/checkpoints"
_LOCAL_OUTPUT_ROOT = "/tmp/receipt_layoutlm"


def _resolve_output_dir(job_name: str) -> str:
    """Pick the trainer's checkpoint directory.

    When running inside a SageMaker BYOC container with managed-spot
    checkpointing enabled, SageMaker mounts /opt/ml/checkpoints and
    auto-syncs it with the S3 path configured in ``CheckpointConfig``
    — both *during* training (so checkpoints survive a spot
    interruption) and *at startup* on the replacement instance (so the
    trainer's auto-resume can pick up where the previous run left off).

    Writing to /tmp/receipt_layoutlm/{job_name}/ defeats that
    mechanism: the spot-restart pulls an empty /opt/ml/checkpoints/
    back from S3, the trainer's auto-resume looks in /tmp/ and finds
    nothing, and training silently starts over from epoch 0.

    Resolution order:
      1. /opt/ml/checkpoints (when SageMaker mounted it)
      2. /tmp/receipt_layoutlm/{job_name} (local dev fallback)

    ``job_name`` is interpolated into the fallback path, so we validate
    it can't contain path separators or ".." segments that would let
    the path escape the local root.
    """
    if os.path.isdir(SAGEMAKER_CHECKPOINT_DIR):
        return SAGEMAKER_CHECKPOINT_DIR

    root = os.path.realpath(_LOCAL_OUTPUT_ROOT)
    candidate = os.path.realpath(os.path.join(root, job_name))
    if candidate == root or os.path.commonpath([root, candidate]) != root:
        raise ValueError(
            f"Invalid job_name {job_name!r}: would escape {root}"
        )
    return candidate


class ReceiptLayoutLMTrainer:
    def __init__(
        self,
        data_config: DataConfig,
        training_config: TrainingConfig,
    ) -> None:
        self.data_config = data_config
        self.training_config = training_config

        self.dynamo = DynamoClient(
            table_name=self.data_config.dynamo_table_name,
            region=self.data_config.aws_region,
        )
        # Use DynamoClient directly for job-related writes

        transformers = importlib.import_module("transformers")
        self._transformers = transformers
        self._torch = importlib.import_module("torch")

        # Enable TF32 for faster matmul on Ampere GPUs (A10G)
        if hasattr(self._torch.backends, "cuda"):
            try:
                self._torch.backends.cuda.matmul.allow_tf32 = True  # type: ignore[attr-defined]
            except AttributeError:
                pass
        if hasattr(self._torch, "set_float32_matmul_precision"):
            try:
                self._torch.set_float32_matmul_precision("medium")  # type: ignore[attr-defined]
            except AttributeError:
                pass
        if hasattr(self._torch.backends, "cudnn"):
            try:
                self._torch.backends.cudnn.allow_tf32 = True  # type: ignore[attr-defined]
                self._torch.backends.cudnn.benchmark = True  # type: ignore[attr-defined]
            except AttributeError:
                pass

        if self.training_config.model_version == "v3":
            self.tokenizer = transformers.LayoutLMv3TokenizerFast.from_pretrained(
                self.training_config.pretrained_model_name
            )
            self._image_processor = transformers.LayoutLMv3ImageProcessor.from_pretrained(
                self.training_config.pretrained_model_name
            )
            self._image_processor.apply_ocr = False
        else:
            self.tokenizer = transformers.LayoutLMTokenizerFast.from_pretrained(
                self.training_config.pretrained_model_name
            )
            self._image_processor = None

    def _label_list(self, dataset) -> List[str]:
        tags = set()
        # dataset["ner_tags"] is a list of lists (per-line sequences)
        for seq in dataset["ner_tags"]:
            tags.update(seq)
        # Ensure 'O' exists and index 0 is allowed by Trainer
        labels = sorted(tags - {"O"})
        return ["O", *labels]

    def train(self, job_name: str, created_by: str = "system") -> str:
        # Derive output directory early for run logging. Inside SageMaker
        # with managed-spot checkpointing this is /opt/ml/checkpoints (so
        # CheckpointConfig auto-syncs it ↔ S3 and the trainer's
        # auto-resume can survive spot interruptions); falls back to
        # /tmp/receipt_layoutlm/{job_name} for local dev.
        output_dir = _resolve_output_dir(job_name)
        # Best-effort create output directory
        os.makedirs(output_dir, exist_ok=True)

        # Get effective label merges from config
        effective_label_merges = self.data_config.get_effective_label_merges()

        # Allow loading a prebuilt tokenized dataset snapshot
        # Note: When loading from snapshot, split_metadata and merge_info will be
        # None/empty since metadata isn't saved with tokenized datasets.
        ds_mod = importlib.import_module("datasets")
        split_metadata = None
        merge_info: MergeInfo | None = None
        if self.data_config.dataset_snapshot_load:
            try:
                datasets = ds_mod.load_from_disk(
                    self.data_config.dataset_snapshot_load
                )
                # Create a minimal merge_info for snapshot loads
                merge_info = MergeInfo(
                    label_merges=effective_label_merges,
                    merge_lookup={},
                    resulting_labels=[],
                )
            except (FileNotFoundError, OSError, ValueError):
                # Fallback to normal path if load fails
                datasets, split_metadata, merge_info = load_datasets(
                    self.dynamo,
                    label_merges=effective_label_merges,
                    allowed_labels=self.data_config.allowed_labels,
                    model_version=self.training_config.model_version,
                    item_window_augmentation=(
                        self.data_config.item_window_augmentation
                    ),
                    item_window_size=self.data_config.item_window_size,
                    item_window_stride=self.data_config.item_window_stride,
                )
        else:
            datasets, split_metadata, merge_info = load_datasets(
                self.dynamo,
                label_merges=effective_label_merges,
                allowed_labels=self.data_config.allowed_labels,
                model_version=self.training_config.model_version,
                item_window_augmentation=(
                    self.data_config.item_window_augmentation
                ),
                item_window_size=self.data_config.item_window_size,
                item_window_stride=self.data_config.item_window_stride,
            )

        # Compute dataset counts prior to tokenization for run logging
        def _count_labels(dataset) -> Dict[str, Any]:
            counts: Dict[str, int] = {}
            num_lines = 0
            num_tokens = 0
            num_entity_tokens = 0
            num_all_o_lines = 0

            for ex in dataset:
                tags = ex["ner_tags"]
                num_lines += 1
                num_tokens += len(tags)
                line_all_o = True
                for t in tags:
                    counts[t] = counts.get(t, 0) + 1
                    if t != "O":
                        num_entity_tokens += 1
                        line_all_o = False
                if line_all_o:
                    num_all_o_lines += 1

            # Sort non-O labels for readability
            non_o_sorted = sorted(
                ((k, v) for k, v in counts.items() if k != "O"),
                key=lambda kv: kv[1],
                reverse=True,
            )
            o_count = counts.get("O", 0)
            return {
                "num_lines": num_lines,
                "num_tokens": num_tokens,
                "num_entity_tokens": num_entity_tokens,
                "num_o_tokens": o_count,
                "o_entity_ratio": (
                    (float(o_count) / float(num_entity_tokens))
                    if num_entity_tokens
                    else None
                ),
                "label_counts": {k: counts[k] for k, _ in non_o_sorted},
            }

        dataset_counts: Dict[str, Any] = {}
        for split in ("train", "validation"):
            if split in datasets:
                dataset_counts[split] = _count_labels(datasets[split])
        label_list = (
            self._label_list(datasets["train"])
            if len(datasets["train"])
            else ["O"]
        )

        label2id: Dict[str, int] = {
            label: i for i, label in enumerate(label_list)
        }
        id2label: Dict[int, str] = {
            i: label for i, label in enumerate(label_list)
        }

        if self.training_config.model_version == "v3":
            model_cls = self._transformers.LayoutLMv3ForTokenClassification
        else:
            model_cls = self._transformers.LayoutLMForTokenClassification

        model = model_cls.from_pretrained(
            self.training_config.pretrained_model_name,
            num_labels=len(label_list),
            id2label=id2label,
            label2id=label2id,
        )

        # Initialize category-aware label embeddings if enabled
        self._initialize_category_aware_embeddings(model, label2id)

        collator = self._transformers.DataCollatorForTokenClassification(
            tokenizer=self.tokenizer
        )

        # Shared label/bbox alignment for both v1 and v3
        def _align_labels_and_bboxes(encoding, example, label2id):
            word_ids = encoding.word_ids()
            seq_len = len(encoding["input_ids"])
            labels = []
            bboxes = []
            prev_word_id = None
            for i in range(seq_len):
                wid = word_ids[i]
                if wid is None:
                    labels.append(-100)
                    bboxes.append([0, 0, 0, 0])
                else:
                    if wid != prev_word_id:
                        lbl = example["ner_tags"][wid]
                        labels.append(label2id.get(lbl, 0))
                    else:
                        labels.append(-100)
                    bboxes.append(example["bboxes"][wid])
                    prev_word_id = wid
            encoding["labels"] = labels
            encoding["bbox"] = bboxes

        def _preprocess(example, tokenizer, label2id, max_len: int):
            encoding = tokenizer(
                example["tokens"],
                is_split_into_words=True,
                truncation=True,
                padding="max_length",
                max_length=max_len,
                return_attention_mask=True,
            )
            _align_labels_and_bboxes(encoding, example, label2id)
            return encoding

        def _preprocess_v3(
            example, tokenizer, label2id, max_len: int, image_processor, image_cache_dir
        ):
            from PIL import Image as PILImage

            # v3 tokenizer takes words + boxes together and aligns bboxes to subtokens
            encoding = tokenizer(
                text=example["tokens"],
                boxes=example["bboxes"],
                truncation=True,
                padding="max_length",
                max_length=max_len,
                return_attention_mask=True,
            )
            # Align labels using word_ids (same logic as v1, but bbox already handled by v3 tokenizer)
            word_ids = encoding.word_ids()
            seq_len = len(encoding["input_ids"])
            labels = []
            prev_word_id = None
            for i in range(seq_len):
                wid = word_ids[i]
                if wid is None:
                    labels.append(-100)
                else:
                    if wid != prev_word_id:
                        lbl = example["ner_tags"][wid]
                        labels.append(label2id.get(lbl, 0))
                    else:
                        labels.append(-100)
                    prev_word_id = wid
            encoding["labels"] = labels

            # receipt_key format: "image_id#00001" — parse for warped receipt cache lookup
            parts = example["receipt_key"].split("#")
            receipt_id = int(parts[1]) if len(parts) == 2 else 1
            img_path = os.path.join(image_cache_dir, f"{parts[0]}_{receipt_id}.png")
            if os.path.exists(img_path):
                image = PILImage.open(img_path).convert("RGB")
            else:
                image = PILImage.new("RGB", (224, 224), (128, 128, 128))
            pixel_values = image_processor(images=image, return_tensors="pt")[
                "pixel_values"
            ].squeeze(0)
            encoding["pixel_values"] = pixel_values

            if "token_type_ids" in encoding:
                del encoding["token_type_ids"]

            return encoding

        if self.training_config.model_version == "v3":
            image_cache_dir = (
                split_metadata.image_cache_dir
                if split_metadata and split_metadata.image_cache_dir
                else "/tmp/receipt_images"
            )
            preprocess = partial(
                _preprocess_v3,
                tokenizer=self.tokenizer,
                label2id=label2id,
                max_len=self.data_config.max_seq_length,
                image_processor=self._image_processor,
                image_cache_dir=image_cache_dir,
            )
        else:
            preprocess = partial(
                _preprocess,
                tokenizer=self.tokenizer,
                label2id=label2id,
                max_len=self.data_config.max_seq_length,
            )

        # Columns to remove during preprocessing — filter to those actually present
        # (v1 datasets already had receipt_key removed in data_loader)
        candidate_cols = ["tokens", "bboxes", "ner_tags", "image_id", "receipt_key"]
        remove_cols = [c for c in candidate_cols if c in datasets["train"].column_names]

        # Parallelize preprocessing across CPU cores
        # Disable cache to minimize local disk usage on SageMaker instances
        num_proc = max(1, min(os.cpu_count() or 1, 8))
        datasets = datasets.map(  # type: ignore[attr-defined]
            preprocess,
            remove_columns=remove_cols,
            num_proc=num_proc,
            desc="Tokenize+align",
            load_from_cache_file=False,  # Don't cache to disk
        )

        # Optionally save tokenized dataset snapshot for reuse
        if self.data_config.dataset_snapshot_save:
            try:
                datasets.save_to_disk(self.data_config.dataset_snapshot_save)
            except (OSError, PermissionError, ValueError):
                pass

        # Version-compatible TrainingArguments
        args_kwargs = dict(
            # Isolate runs per job to avoid incompatible checkpoint resumes
            output_dir=output_dir,
            per_device_train_batch_size=self.training_config.batch_size,
            per_device_eval_batch_size=self.training_config.batch_size,
            learning_rate=self.training_config.learning_rate,
            weight_decay=self.training_config.weight_decay,
            num_train_epochs=self.training_config.epochs,
            warmup_ratio=self.training_config.warmup_ratio,
            max_grad_norm=self.training_config.max_grad_norm,
            gradient_accumulation_steps=self.training_config.gradient_accumulation_steps,
            logging_steps=50,
            fp16=self.training_config.mixed_precision
            and self._torch.cuda.is_available(),
        )
        ta_cls = self._transformers.TrainingArguments
        ta_params = inspect.signature(ta_cls.__init__).parameters
        seqeval_available = True
        try:
            importlib.import_module("seqeval.metrics")
        except ModuleNotFoundError:
            seqeval_available = False
        checkpoint_metric = _checkpoint_metric_for_available_metrics(
            self.training_config.checkpoint_metric,
            seqeval_available=seqeval_available,
        )
        if "evaluation_strategy" in ta_params:
            args_kwargs["evaluation_strategy"] = "epoch"
        elif "eval_strategy" in ta_params:
            args_kwargs["eval_strategy"] = "epoch"
        if "save_strategy" in ta_params:
            args_kwargs["save_strategy"] = "epoch"
        if "load_best_model_at_end" in ta_params:
            args_kwargs["load_best_model_at_end"] = True
        if "metric_for_best_model" in ta_params:
            args_kwargs["metric_for_best_model"] = checkpoint_metric
        if "greater_is_better" in ta_params:
            args_kwargs["greater_is_better"] = _metric_greater_is_better(
                checkpoint_metric
            )
        # Keep only 2 checkpoints locally to minimize disk usage
        # on_save callback syncs to S3 before old checkpoints are deleted
        if "save_total_limit" in ta_params:
            args_kwargs["save_total_limit"] = 2
        if "save_safetensors" in ta_params:
            args_kwargs["save_safetensors"] = True
        if "label_smoothing_factor" in ta_params:
            args_kwargs["label_smoothing_factor"] = (
                self.training_config.label_smoothing
            )
        # Performance-related knobs (only if supported by current transformers)
        if "dataloader_num_workers" in ta_params:
            args_kwargs["dataloader_num_workers"] = min(
                8, max(2, (os.cpu_count() or 4) // 2)
            )
        if "dataloader_pin_memory" in ta_params:
            args_kwargs["dataloader_pin_memory"] = True
        if "group_by_length" in ta_params:
            args_kwargs["group_by_length"] = True
        if "gradient_checkpointing" in ta_params:
            if self.training_config.model_version != "v3":
                args_kwargs["gradient_checkpointing"] = True
        if "optim" in ta_params:
            args_kwargs["optim"] = "adamw_torch"
        # Enable torch.compile only if Triton backend is available
        # Disabled due to triton version compatibility issues
        if "torch_compile" in ta_params:
            enable_compile = False
            # Temporarily disabled - triton version conflicts
            # try:
            #     importlib.import_module("triton")
            #     importlib.import_module("torch._inductor")
            #     enable_compile = True
            # except (ModuleNotFoundError, ImportError):
            #     enable_compile = False
            args_kwargs["torch_compile"] = enable_compile
        if "fp16_full_eval" in ta_params:
            args_kwargs["fp16_full_eval"] = True
        # Early stopping if available
        if "load_best_model_at_end" in ta_params:
            args_kwargs["load_best_model_at_end"] = True
        callbacks = []
        try:
            es_cls = getattr(self._transformers, "EarlyStoppingCallback")
            callbacks.append(
                es_cls(
                    early_stopping_patience=self.training_config.early_stopping_patience
                )
            )
        except AttributeError:
            pass

        # Note: _MetricLoggerCallback will be created after job is created (see below)
        if "remove_unused_columns" in ta_params:
            args_kwargs["remove_unused_columns"] = False

        training_args = ta_cls(**args_kwargs)

        # Write initial run.json (configs + dataset counts + merge info)
        run_json_path = os.path.join(output_dir, "run.json")
        run_payload = {
            "job_name": job_name,
            "training_config": asdict(self.training_config),
            "data_config": asdict(self.data_config),
            "label_list": label_list,
            "num_labels": len(label_list),
            "dataset_counts": dataset_counts,
            "split_metadata": (
                asdict(split_metadata) if split_metadata else None
            ),
            "label_merges": merge_info.label_merges if merge_info else None,
            "resulting_labels": (
                merge_info.resulting_labels if merge_info else []
            ),
            "epoch_metrics": [],
        }
        with open(run_json_path, "w", encoding="utf-8") as f:
            json.dump(run_payload, f, indent=2)

        # Build job_config with explicit merge info for experiment tracking
        job_config_dict = {
            "model": f"layoutlm-{self.training_config.model_version}",
            **asdict(self.training_config),
            **asdict(self.data_config),
            # Explicit merge info for reproducibility and comparison
            "label_merges": merge_info.label_merges if merge_info else None,
            "resulting_label_set": (
                merge_info.resulting_labels if merge_info else []
            ),
        }

        # Build storage info for S3 model artifacts
        # This enables best_dir_uri() to return the correct path for CoreML export
        storage: Optional[Dict[str, str]] = None
        s3_path = self.training_config.output_s3_path
        if s3_path:
            if s3_path.startswith("s3://"):
                parsed = urlparse(s3_path)
                bucket = parsed.netloc
                prefix = parsed.path.lstrip("/")
                if not prefix.endswith("/"):
                    prefix += "/"
                run_prefix = f"{prefix}{job_name}/"
            else:
                bucket = s3_path
                run_prefix = f"runs/{job_name}/"
            storage = {
                "bucket": bucket,
                "run_root_prefix": run_prefix,
            }

        job = Job(
            job_id=str(uuid.uuid4()),
            name=job_name,
            description="LayoutLM receipt token classification",
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by=created_by,
            status="pending",
            priority="medium",
            job_config=job_config_dict,
            estimated_duration=None,
            tags={},
            storage=storage,
        )
        self.dynamo.add_job(job)

        # Write label merge config as JobMetric for experiment tracking
        if merge_info and merge_info.label_merges:
            try:
                merge_metric = JobMetric(
                    job_id=job.job_id,
                    metric_name="label_merge_config",
                    value=merge_info.label_merges,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    unit="config",
                    epoch=None,
                )
                self.dynamo.add_job_metric(merge_metric)
            except Exception as e:
                print(
                    f"Warning: Failed to write label merge config metric: {e}"
                )

        # Update status to running
        job.status = "running"
        try:
            self.dynamo.update_job(job)
        except Exception as e:
            print(f"Warning: Failed to update job status to running: {e}")

        # Per-epoch metric logging to run.json (created after job so we have job_id)
        try:
            TrainerCallback = getattr(self._transformers, "TrainerCallback")

            class _MetricLoggerCallback(TrainerCallback):  # type: ignore
                def __init__(
                    self,
                    run_path: str,
                    job_id: str,
                    dynamo_client: Any,
                    s3_bucket: str | None = None,
                    s3_prefix: str | None = None,
                ) -> None:
                    self.run_path = run_path
                    self.job_id = job_id
                    self.dynamo = dynamo_client
                    self.s3_bucket = s3_bucket
                    self.s3_prefix = s3_prefix
                    self._synced_checkpoints: set = set()

                def on_evaluate(self, args, state, control, metrics=None, **kwargs):  # type: ignore[override]
                    # Silence unused parameter warnings
                    del args, control, kwargs
                    if not self.run_path:
                        return
                    data = {}
                    try:
                        with open(self.run_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except (FileNotFoundError, json.JSONDecodeError):
                        data = {}
                    epoch_metrics = data.get("epoch_metrics", [])

                    current_epoch = (
                        float(state.epoch)
                        if getattr(state, "epoch", None)
                        else None
                    )

                    entry = {
                        "epoch": current_epoch,
                        "global_step": int(getattr(state, "global_step", 0)),
                    }

                    # Set up metric recording (used for both eval metrics and training metrics)
                    epoch_val = (
                        int(current_epoch)
                        if current_epoch is not None
                        else None
                    )
                    step_val = int(getattr(state, "global_step", 0))
                    metrics_to_write: list = []

                    def _add_metric(
                        name: str, value: float, unit: str = "ratio"
                    ):
                        """Add a metric to the batch for writing."""
                        metrics_to_write.append(
                            JobMetric(
                                job_id=self.job_id,
                                metric_name=name,
                                value=float(value),
                                timestamp=datetime.now(
                                    timezone.utc
                                ).isoformat(),
                                unit=unit,
                                epoch=epoch_val,
                                step=step_val,
                            )
                        )

                    if isinstance(metrics, dict):
                        # Add scalar metrics
                        entry.update(
                            {
                                k: float(v)
                                for k, v in metrics.items()
                                if isinstance(v, (int, float))
                            }
                        )

                        # Save per-label metrics to DynamoDB
                        # Keys are like eval_label_MERCHANT_NAME_f1, eval_label_DATE_precision, etc.
                        for key, value in metrics.items():
                            if key.startswith("eval_label_") and isinstance(
                                value, (int, float)
                            ):
                                # Extract metric type (f1, precision, recall, support)
                                # Key format: eval_label_{LABEL}_{metric_type}
                                parts = key.split("_")
                                if len(parts) >= 4:
                                    metric_type = parts[
                                        -1
                                    ]  # f1, precision, recall, support
                                    label_name = "_".join(
                                        parts[2:-1]
                                    )  # Handle labels with underscores
                                    unit = (
                                        "count"
                                        if metric_type == "support"
                                        else "ratio"
                                    )
                                    _add_metric(
                                        f"label_{label_name}_{metric_type}",
                                        float(value),
                                        unit,
                                    )

                        # Collect training metrics for batch write
                        # F1 score
                        f1_value = metrics.get("eval_f1") or metrics.get("f1")
                        if f1_value is not None:
                            _add_metric("val_f1", f1_value, "ratio")

                        # Precision
                        prec_value = metrics.get(
                            "eval_precision"
                        ) or metrics.get("precision")
                        if prec_value is not None:
                            _add_metric("val_precision", prec_value, "ratio")

                        # Recall
                        recall_value = metrics.get(
                            "eval_recall"
                        ) or metrics.get("recall")
                        if recall_value is not None:
                            _add_metric("val_recall", recall_value, "ratio")

                        # Eval loss
                        eval_loss = metrics.get("eval_loss")
                        if eval_loss is not None:
                            _add_metric("eval_loss", eval_loss, "loss")

                        explanation_scalars = {
                            "entity_prediction_rate": "val_entity_prediction_rate",
                            "gold_entity_rate": "val_gold_entity_rate",
                            "entity_prediction_gold_ratio": (
                                "val_entity_prediction_gold_ratio"
                            ),
                            "entity_token_accuracy": "val_entity_token_accuracy",
                            "o_token_accuracy": "val_o_token_accuracy",
                            "product_detail_macro_f1": (
                                "val_product_detail_macro_f1"
                            ),
                            "entropy_mean": "val_entropy_mean",
                            "entropy_std": "val_entropy_std",
                            "entropy_p10": "val_entropy_p10",
                            "entropy_p90": "val_entropy_p90",
                        }
                        for source_name, metric_name in explanation_scalars.items():
                            value = metrics.get(f"eval_{source_name}")
                            if value is None:
                                value = metrics.get(source_name)
                            if value is not None:
                                _add_metric(metric_name, value, "ratio")

                        # Confusion matrix (stored as dict, not scalar)
                        cm_data = metrics.get(
                            "eval_confusion_matrix"
                        ) or metrics.get("confusion_matrix")
                        if cm_data and isinstance(cm_data, dict):
                            metrics_to_write.append(
                                JobMetric(
                                    job_id=self.job_id,
                                    metric_name="confusion_matrix",
                                    value=cm_data,
                                    timestamp=datetime.now(
                                        timezone.utc
                                    ).isoformat(),
                                    unit="matrix",
                                    epoch=epoch_val,
                                    step=step_val,
                                )
                            )

                    # Add training loss from log_history if available
                    if (
                        state
                        and hasattr(state, "log_history")
                        and state.log_history
                    ):
                        # Get the most recent training step log entry
                        train_logs = [
                            log
                            for log in state.log_history
                            if "loss" in log and "eval_loss" not in log
                        ]
                        if train_logs:
                            latest_train = train_logs[-1]
                            if "loss" in latest_train:
                                entry["train_loss"] = float(
                                    latest_train["loss"]
                                )
                                _add_metric(
                                    "train_loss", latest_train["loss"], "loss"
                                )
                            if "learning_rate" in latest_train:
                                entry["learning_rate"] = float(
                                    latest_train["learning_rate"]
                                )
                                _add_metric(
                                    "learning_rate",
                                    latest_train["learning_rate"],
                                    "rate",
                                )

                    # Write metrics to DynamoDB (one at a time since batch method doesn't exist)
                    if metrics_to_write:
                        for metric in metrics_to_write:
                            try:
                                self.dynamo.add_job_metric(metric)
                            except Exception as e:
                                # Log but don't fail training on metric write errors
                                print(
                                    f"Warning: Failed to write metric {metric.metric_name}: {e}"
                                )

                    epoch_metrics.append(entry)
                    data["epoch_metrics"] = epoch_metrics
                    with open(self.run_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)

                def on_save(self, args, state, control, **kwargs):  # type: ignore[override]
                    """Sync checkpoint to S3 after each save."""
                    del control, kwargs  # unused
                    if not self.s3_bucket or not self.s3_prefix:
                        return

                    # Get the checkpoint that was just saved
                    checkpoint_dir = os.path.join(
                        args.output_dir, f"checkpoint-{state.global_step}"
                    )
                    if not os.path.exists(checkpoint_dir):
                        return

                    # Skip if already synced
                    if checkpoint_dir in self._synced_checkpoints:
                        return

                    try:
                        boto3 = importlib.import_module("boto3")
                        s3_client = boto3.client("s3")

                        # Sync checkpoint to S3
                        checkpoint_name = f"checkpoint-{state.global_step}"
                        s3_checkpoint_prefix = (
                            f"{self.s3_prefix}checkpoints/{checkpoint_name}/"
                        )

                        print(
                            f"📤 Syncing {checkpoint_name} to s3://{self.s3_bucket}/{s3_checkpoint_prefix}"
                        )
                        for root, _dirs, files in os.walk(checkpoint_dir):
                            for file in files:
                                local_path = os.path.join(root, file)
                                rel_path = os.path.relpath(
                                    local_path, checkpoint_dir
                                )
                                s3_key = f"{s3_checkpoint_prefix}{rel_path}"
                                s3_client.upload_file(
                                    local_path, self.s3_bucket, s3_key
                                )

                        self._synced_checkpoints.add(checkpoint_dir)
                        print(f"✅ Checkpoint {checkpoint_name} synced to S3")

                        # Also sync run.json so we can track progress
                        if self.run_path and os.path.exists(self.run_path):
                            run_json_key = f"{self.s3_prefix}run.json"
                            s3_client.upload_file(
                                self.run_path, self.s3_bucket, run_json_key
                            )

                    except ModuleNotFoundError:
                        # Fallback to AWS CLI
                        result = subprocess.run(
                            [
                                "aws",
                                "s3",
                                "sync",
                                checkpoint_dir,
                                f"s3://{self.s3_bucket}/{self.s3_prefix}checkpoints/checkpoint-{state.global_step}/",
                            ],
                            capture_output=True,
                            text=True,
                        )
                        if result.returncode == 0:
                            self._synced_checkpoints.add(checkpoint_dir)
                            print("✅ Checkpoint synced to S3")
                        else:
                            print(
                                f"⚠️  Failed to sync checkpoint: {result.stderr}"
                            )
                    except Exception as e:
                        print(f"⚠️  Failed to sync checkpoint to S3: {e}")

            class _HeldoutEvalCallback(TrainerCallback):  # type: ignore
                """Run the windowed held-out eval after each saved checkpoint.

                Reuses the in-process model/data/GPU to emit ``epochs.json`` (the
                viz cache) live — no separate Processing job. Entirely
                best-effort: any failure logs a warning and training proceeds.
                """

                def __init__(
                    self,
                    *,
                    output_dir: str,
                    dynamo_client: Any,
                    val_keys: List[Tuple[str, int]],
                    val_hash: Optional[str],
                    seed: Optional[int],
                    window_size: int,
                    window_stride: int,
                    job_name: str,
                    job_id: str,
                    s3_bucket: str | None,
                    s3_prefix: str | None,
                    valid_status: str = "VALID",
                    num_showcase: int = 5,
                ) -> None:
                    self.output_dir = output_dir
                    self.dynamo = dynamo_client
                    self.val_keys = val_keys
                    self.val_hash = val_hash
                    self.seed = seed
                    self.window_size = window_size
                    self.window_stride = window_stride
                    self.job_name = job_name
                    self.job_id = job_id
                    self.s3_bucket = s3_bucket
                    self.s3_prefix = s3_prefix
                    self.valid_status = valid_status
                    self.num_showcase = num_showcase
                    self._details = None  # lazy-loaded once
                    self._showcase_keys: List[str] = []
                    # Seed from any epochs.json already on disk so a managed-spot
                    # resume (SageMaker re-hydrates output_dir from S3) keeps the
                    # pre-resume F1 curve instead of overwriting it with one entry.
                    self._entries: List[dict] = []
                    try:
                        ep_path = os.path.join(output_dir, "epochs.json")
                        if os.path.exists(ep_path):
                            with open(ep_path, "r", encoding="utf-8") as f:
                                prior = json.load(f)
                            self._entries = [
                                e
                                for e in (prior.get("epochs") or [])
                                if e.get("checkpoint") != "best"
                            ]
                    except Exception as e:  # noqa: BLE001
                        print(f"⚠️  Could not seed epochs.json: {e}")

                def _write_live_metrics(
                    self,
                    entry: Dict[str, Any],
                    *,
                    epoch: Optional[int],
                    step: int,
                ) -> None:
                    def _write(
                        name: str,
                        value: Any,
                        unit: str = "ratio",
                    ) -> None:
                        if not isinstance(value, (int, float)):
                            return
                        try:
                            self.dynamo.add_job_metric(
                                JobMetric(
                                    job_id=self.job_id,
                                    metric_name=name,
                                    value=float(value),
                                    timestamp=datetime.now(
                                        timezone.utc
                                    ).isoformat(),
                                    unit=unit,
                                    epoch=epoch,
                                    step=step,
                                )
                            )
                        except Exception as e:  # noqa: BLE001
                            print(
                                "Warning: Failed to write live held-out "
                                f"metric {name}: {e}"
                            )

                    _write("heldout_windowed_f1", entry.get("heldout_f1"))
                    _write(
                        "heldout_windowed_precision",
                        entry.get("heldout_precision"),
                    )
                    _write(
                        "heldout_windowed_recall",
                        entry.get("heldout_recall"),
                    )
                    _write(
                        "heldout_windowed_token_accuracy",
                        entry.get("token_accuracy"),
                    )
                    _write(
                        "heldout_windowed_product_detail_macro_f1",
                        entry.get("product_detail_macro_f1"),
                    )
                    _write(
                        "heldout_windowed_entity_prediction_rate",
                        entry.get("entity_prediction_rate"),
                    )
                    _write(
                        "heldout_windowed_gold_entity_rate",
                        entry.get("gold_entity_rate"),
                    )
                    _write(
                        "heldout_windowed_f1_delta_vs_training_reported",
                        entry.get("heldout_f1_delta_vs_training_reported"),
                    )
                    _write(
                        "heldout_windowed_avg_inference_ms",
                        entry.get("avg_inference_ms"),
                        "ms",
                    )

                    per_label_f1 = entry.get("per_label_f1") or {}
                    per_label_precision = entry.get("per_label_precision") or {}
                    per_label_recall = entry.get("per_label_recall") or {}
                    per_label_support = entry.get("per_label_support") or {}
                    for label in (
                        "PRODUCT_NAME",
                        "QUANTITY",
                        "UNIT_PRICE",
                        "LINE_TOTAL",
                    ):
                        _write(
                            f"heldout_label_{label}_f1",
                            per_label_f1.get(label),
                        )
                        _write(
                            f"heldout_label_{label}_precision",
                            per_label_precision.get(label),
                        )
                        _write(
                            f"heldout_label_{label}_recall",
                            per_label_recall.get(label),
                        )
                        _write(
                            f"heldout_label_{label}_support",
                            per_label_support.get(label),
                            "count",
                        )

                def _ensure_details(self) -> None:
                    if self._details is not None:
                        return
                    from receipt_layoutlm import evaluate_checkpoints as ec

                    # Pin the inference window to the values training used so the
                    # held-out scoring matches the model's training distribution.
                    os.environ["LAYOUTLM_WINDOW_SIZE"] = str(self.window_size)
                    os.environ["LAYOUTLM_WINDOW_STRIDE"] = str(
                        self.window_stride
                    )
                    self._details = ec.load_val_details(
                        self.dynamo, self.val_keys
                    )
                    self._showcase_keys = [
                        f"{i}_{r}"
                        for (i, r) in self.val_keys[: self.num_showcase]
                    ]

                def on_save(self, args, state, control, **kwargs):  # type: ignore[override]
                    del control, kwargs
                    try:
                        from receipt_layoutlm import evaluate_checkpoints as ec

                        self._ensure_details()
                        if not self._details:
                            return
                        checkpoint_dir = os.path.join(
                            args.output_dir,
                            f"checkpoint-{state.global_step}",
                        )
                        if not os.path.exists(checkpoint_dir):
                            return

                        epoch_num = (
                            int(round(state.epoch))
                            if state.epoch is not None
                            else None
                        )
                        reported = None
                        for log in reversed(state.log_history or []):
                            if "eval_f1" in log:
                                reported = log["eval_f1"]
                                break

                        entry, _ll, _lm = ec.evaluate_live_checkpoint(
                            checkpoint_dir,
                            self._details,
                            output_dir=self.output_dir,
                            step=state.global_step,
                            epoch_num=epoch_num,
                            training_reported_f1=reported,
                            showcase_keys=self._showcase_keys,
                            valid_status=self.valid_status,
                        )
                        self._entries.append(entry)
                        self._write_live_metrics(
                            entry,
                            epoch=epoch_num,
                            step=int(getattr(state, "global_step", 0)),
                        )
                        run_s3_uri = (
                            f"s3://{self.s3_bucket}/{self.s3_prefix}"
                            if self.s3_bucket and self.s3_prefix
                            else ""
                        )
                        ec.write_epochs_json_live(
                            self.output_dir,
                            job_name=self.job_name,
                            run_s3_uri=run_s3_uri,
                            epoch_entries=self._entries,
                            label_list=_ll,
                            label_merges=_lm,
                            window_size=self.window_size,
                            window_stride=self.window_stride,
                            val_set_source="persisted_val_receipt_keys",
                            val_hash=self.val_hash,
                            num_val_receipts=len(self._details),
                            seed=self.seed,
                            showcase_keys=self._showcase_keys,
                        )
                        print(
                            f"📈 Live held-out eval epoch {epoch_num}: "
                            f"F1={entry['heldout_f1']:.4f} "
                            f"({entry.get('avg_inference_ms')}ms/receipt)"
                        )
                        # Sync epochs.json + showcase receipts to S3.
                        if self.s3_bucket and self.s3_prefix:
                            try:
                                ec.sync_outputs_to_s3(
                                    self.output_dir,
                                    f"s3://{self.s3_bucket}/{self.s3_prefix}",
                                )
                            except Exception as se:  # noqa: BLE001
                                print(
                                    f"⚠️  Failed to sync epochs.json: {se}"
                                )
                    except Exception as e:  # noqa: BLE001
                        # Never let the held-out eval break training.
                        print(f"⚠️  Live held-out eval failed: {e}")

            # Parse S3 config for per-epoch checkpoint syncing
            s3_bucket = None
            s3_prefix = None
            if self.training_config.output_s3_path:
                s3_path = self.training_config.output_s3_path
                if s3_path.startswith("s3://"):
                    parsed = urlparse(s3_path)
                    s3_bucket = parsed.netloc
                    s3_prefix = parsed.path.lstrip("/")
                    if not s3_prefix.endswith("/"):
                        s3_prefix += "/"
                    s3_prefix = f"{s3_prefix}{job_name}/"
                else:
                    s3_bucket = s3_path
                    s3_prefix = f"runs/{job_name}/"

            callbacks.append(
                _MetricLoggerCallback(
                    os.path.join(output_dir, "run.json"),
                    job.job_id,
                    self.dynamo,
                    s3_bucket=s3_bucket,
                    s3_prefix=s3_prefix,
                )
            )

            # Live held-out eval (emits epochs.json during training). Needs the
            # persisted val keys; older split logic without them is skipped.
            val_keys_raw = (
                getattr(split_metadata, "val_receipt_keys", None)
                if split_metadata
                else None
            )
            if (
                self.training_config.eval_heldout_windowed
                and val_keys_raw
            ):
                parsed_val_keys: List[Tuple[str, int]] = []
                for k in val_keys_raw:
                    try:
                        img, rid = k.split("#")
                        parsed_val_keys.append((img, int(rid)))
                    except (ValueError, AttributeError):
                        continue
                callbacks.append(
                    _HeldoutEvalCallback(
                        output_dir=output_dir,
                        dynamo_client=self.dynamo,
                        val_keys=parsed_val_keys,
                        val_hash=getattr(
                            split_metadata, "val_receipts_hash", None
                        ),
                        seed=getattr(split_metadata, "random_seed", None),
                        window_size=(
                            getattr(split_metadata, "window_size", None) or 200
                        ),
                        window_stride=(
                            getattr(split_metadata, "window_stride", None)
                            or 150
                        ),
                        job_name=job_name,
                        job_id=job.job_id,
                        s3_bucket=s3_bucket,
                        s3_prefix=s3_prefix,
                        valid_status=self.data_config.validation_status,
                    )
                )
        except AttributeError:
            # Older transformers versions may lack TrainerCallback
            pass

        # Store initial run.json content in Dynamo as a JobLog entry
        try:
            from receipt_dynamo.data.shared_exceptions import (
                DynamoDBError,
                EntityError,
                OperationError,
            )

            cfg_log = JobLog(
                job_id=job.job_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                log_level="INFO",
                message=json.dumps(
                    {"type": "run_config", "data": run_payload}
                ),
                source="receipt_layoutlm.trainer",
            )
            self.dynamo.add_job_log(cfg_log)

            # Write dataset quality metrics as individual JobMetric records
            dataset_metrics: List[JobMetric] = []
            ts = datetime.now(timezone.utc).isoformat()

            # Training set metrics
            if "train" in dataset_counts:
                train_stats = dataset_counts["train"]
                dataset_metrics.append(
                    JobMetric(
                        job_id=job.job_id,
                        metric_name="num_train_samples",
                        value=train_stats.get("num_lines", 0),
                        timestamp=ts,
                        unit="count",
                        epoch=None,
                    )
                )
                if train_stats.get("o_entity_ratio") is not None:
                    dataset_metrics.append(
                        JobMetric(
                            job_id=job.job_id,
                            metric_name="o_entity_ratio_train",
                            value=train_stats["o_entity_ratio"],
                            timestamp=ts,
                            unit="ratio",
                            epoch=None,
                        )
                    )

            # Validation set metrics
            if "validation" in dataset_counts:
                val_stats = dataset_counts["validation"]
                dataset_metrics.append(
                    JobMetric(
                        job_id=job.job_id,
                        metric_name="num_val_samples",
                        value=val_stats.get("num_lines", 0),
                        timestamp=ts,
                        unit="count",
                        epoch=None,
                    )
                )
                if val_stats.get("o_entity_ratio") is not None:
                    dataset_metrics.append(
                        JobMetric(
                            job_id=job.job_id,
                            metric_name="o_entity_ratio_val",
                            value=val_stats["o_entity_ratio"],
                            timestamp=ts,
                            unit="ratio",
                            epoch=None,
                        )
                    )

            # Add split metadata metrics if available
            if split_metadata:
                dataset_metrics.extend(
                    [
                        JobMetric(
                            job_id=job.job_id,
                            metric_name="random_seed",
                            value=split_metadata.random_seed,
                            timestamp=ts,
                            unit="seed",
                            epoch=None,
                        ),
                        JobMetric(
                            job_id=job.job_id,
                            metric_name="num_train_receipts",
                            value=split_metadata.num_train_receipts,
                            timestamp=ts,
                            unit="count",
                            epoch=None,
                        ),
                        JobMetric(
                            job_id=job.job_id,
                            metric_name="num_val_receipts",
                            value=split_metadata.num_val_receipts,
                            timestamp=ts,
                            unit="count",
                            epoch=None,
                        ),
                        JobMetric(
                            job_id=job.job_id,
                            metric_name="o_entity_ratio_before_downsample",
                            value=split_metadata.o_entity_ratio_before_downsample,
                            timestamp=ts,
                            unit="ratio",
                            epoch=None,
                        ),
                        JobMetric(
                            job_id=job.job_id,
                            metric_name="o_only_lines_dropped",
                            value=split_metadata.o_only_lines_dropped,
                            timestamp=ts,
                            unit="count",
                            epoch=None,
                        ),
                        JobMetric(
                            job_id=job.job_id,
                            metric_name="target_o_entity_ratio",
                            value=split_metadata.target_o_entity_ratio,
                            timestamp=ts,
                            unit="ratio",
                            epoch=None,
                        ),
                    ]
                )

            # Write metrics one at a time (batch method doesn't exist)
            for metric in dataset_metrics:
                try:
                    self.dynamo.add_job_metric(metric)
                except Exception as e:
                    print(
                        f"Warning: Failed to write metric {metric.metric_name}: {e}"
                    )

        except (DynamoDBError, EntityError, OperationError, ValueError) as e:
            print(f"Warning: Failed to write dataset metrics: {e}")

        def compute_metrics(eval_pred):
            # seqeval F1 at entity-level (BIO tags)
            try:
                seqeval = importlib.import_module("seqeval.metrics")
                f1_fn = getattr(seqeval, "f1_score")
                precision_fn = getattr(seqeval, "precision_score")
                recall_fn = getattr(seqeval, "recall_score")
                classification_report_fn = getattr(
                    seqeval, "classification_report", None
                )
            except ModuleNotFoundError:
                # Fallback to token accuracy if seqeval unavailable
                seqeval = None
                classification_report_fn = None

            logits, labels = eval_pred
            preds = logits.argmax(-1)

            # Convert ids to tag strings, ignoring -100
            id2label_local = id2label
            y_true: List[List[str]] = []
            y_pred: List[List[str]] = []
            for true_row, pred_row in zip(labels, preds):
                t_tags: List[str] = []
                p_tags: List[str] = []
                for t, p in zip(true_row, pred_row):
                    if int(t) == -100:
                        continue
                    t_tags.append(id2label_local.get(int(t), "O"))
                    p_tags.append(id2label_local.get(int(p), "O"))
                y_true.append(t_tags)
                y_pred.append(p_tags)

            total_valid_tokens = sum(len(seq) for seq in y_true)
            correct_valid_tokens = sum(
                1
                for true_seq, pred_seq in zip(y_true, y_pred)
                for true_tag, pred_tag in zip(true_seq, pred_seq)
                if true_tag == pred_tag
            )
            metrics = {
                "accuracy": (
                    correct_valid_tokens / total_valid_tokens
                    if total_valid_tokens
                    else 0.0
                )
            }
            if seqeval:
                f1 = float(f1_fn(y_true, y_pred))
                prec = float(precision_fn(y_true, y_pred))
                rec = float(recall_fn(y_true, y_pred))
                metrics.update({"f1": f1, "precision": prec, "recall": rec})

                # Get per-label metrics if classification_report is available
                # Flatten into scalar keys so HuggingFace Trainer passes them to callbacks
                if classification_report_fn:
                    try:
                        # seqeval's classification_report doesn't take a labels param
                        # It automatically computes metrics for all labels in the data
                        report = classification_report_fn(
                            y_true, y_pred, output_dict=True, zero_division=0
                        )
                        # Extract per-label metrics as flattened scalar keys
                        # Skip aggregate keys (micro avg, macro avg, weighted avg)
                        skip_keys = {"micro avg", "macro avg", "weighted avg"}
                        for label, label_metrics in report.items():
                            if label not in skip_keys and isinstance(
                                label_metrics, dict
                            ):
                                # Use flattened keys like label_MERCHANT_NAME_f1
                                metrics[f"label_{label}_f1"] = float(
                                    label_metrics.get("f1-score", 0.0)
                                )
                                metrics[f"label_{label}_precision"] = float(
                                    label_metrics.get("precision", 0.0)
                                )
                                metrics[f"label_{label}_recall"] = float(
                                    label_metrics.get("recall", 0.0)
                                )
                                metrics[f"label_{label}_support"] = int(
                                    label_metrics.get("support", 0)
                                )
                    except Exception as e:
                        # If classification_report fails, continue without per-label metrics
                        print(
                            f"Warning: Failed to compute per-label metrics: {e}"
                        )

                # Compute confusion matrix at token level
                try:
                    from sklearn.metrics import confusion_matrix as sklearn_cm

                    # Flatten sequences for sklearn
                    y_true_flat = [tag for seq in y_true for tag in seq]
                    y_pred_flat = [tag for seq in y_pred for tag in seq]

                    # Get unique labels (sorted for consistent ordering)
                    unique_labels = sorted(set(y_true_flat) | set(y_pred_flat))

                    cm = sklearn_cm(
                        y_true_flat, y_pred_flat, labels=unique_labels
                    )
                    metrics["confusion_matrix"] = {
                        "labels": unique_labels,
                        "matrix": cm.tolist(),
                    }
                except Exception as e:
                    print(f"Warning: Failed to compute confusion matrix: {e}")

                # Compute prediction entropy (measures model confidence)
                # Low entropy = overconfident, high entropy = uncertain
                try:
                    import numpy as _np
                    from scipy.special import softmax as scipy_softmax

                    # Compute softmax probabilities from logits
                    probs = scipy_softmax(logits, axis=-1)

                    # Compute entropy for each token: H = -sum(p * log(p))
                    # Add small epsilon to avoid log(0)
                    log_probs = _np.log(probs + 1e-10)
                    token_entropy = -_np.sum(probs * log_probs, axis=-1)

                    # Mask out padding tokens (where label == -100)
                    mask = labels != -100
                    valid_entropy = token_entropy[mask]

                    if len(valid_entropy) > 0:
                        metrics["entropy_mean"] = float(
                            _np.mean(valid_entropy)
                        )
                        metrics["entropy_std"] = float(_np.std(valid_entropy))
                        # Also track entropy percentiles for distribution insight
                        metrics["entropy_p10"] = float(
                            _np.percentile(valid_entropy, 10)
                        )
                        metrics["entropy_p90"] = float(
                            _np.percentile(valid_entropy, 90)
                        )
                except Exception as e:
                    print(f"Warning: Failed to compute entropy metrics: {e}")

                try:
                    def _base(tag: str) -> str:
                        if tag == "O":
                            return "O"
                        if tag.startswith("B-") or tag.startswith("I-"):
                            return tag[2:]
                        return tag

                    total_tokens = 0
                    gold_entity_tokens = 0
                    pred_entity_tokens = 0
                    correct_entity_tokens = 0
                    gold_o_tokens = 0
                    correct_o_tokens = 0
                    for true_seq, pred_seq in zip(y_true, y_pred):
                        for true_tag, pred_tag in zip(true_seq, pred_seq):
                            gold = _base(true_tag)
                            pred = _base(pred_tag)
                            total_tokens += 1
                            if gold == "O":
                                gold_o_tokens += 1
                                if pred == "O":
                                    correct_o_tokens += 1
                            else:
                                gold_entity_tokens += 1
                                if gold == pred:
                                    correct_entity_tokens += 1
                            if pred != "O":
                                pred_entity_tokens += 1

                    if total_tokens:
                        metrics["gold_entity_rate"] = (
                            gold_entity_tokens / total_tokens
                        )
                        metrics["entity_prediction_rate"] = (
                            pred_entity_tokens / total_tokens
                        )
                    if gold_entity_tokens:
                        metrics["entity_prediction_gold_ratio"] = (
                            pred_entity_tokens / gold_entity_tokens
                        )
                        metrics["entity_token_accuracy"] = (
                            correct_entity_tokens / gold_entity_tokens
                        )
                    if gold_o_tokens:
                        metrics["o_token_accuracy"] = (
                            correct_o_tokens / gold_o_tokens
                        )

                    product_f1_values = [
                        metrics[f"label_{label}_f1"]
                        for label in (
                            "PRODUCT_NAME",
                            "QUANTITY",
                            "UNIT_PRICE",
                            "LINE_TOTAL",
                        )
                        if isinstance(
                            metrics.get(f"label_{label}_f1"), (int, float)
                        )
                    ]
                    if product_f1_values:
                        metrics["product_detail_macro_f1"] = sum(
                            product_f1_values
                        ) / len(product_f1_values)
                except Exception as e:
                    print(
                        f"Warning: Failed to compute explanation metrics: {e}"
                    )

                # Note: F1 metric is now stored in _MetricLoggerCallback.on_evaluate()
                # with epoch information, so we don't need to store it here
            else:
                # Token accuracy fallback
                import numpy as _np

                mask = labels != -100
                correct = (preds == labels) & mask
                acc = (
                    float(_np.sum(correct) / _np.sum(mask))
                    if _np.sum(mask)
                    else 0.0
                )
                metrics = {"accuracy": acc}

            return metrics

        # Class-weighted cross-entropy to combat majority-class ("O") collapse.
        # Per-receipt-window training has O:entity ratio ~3.6:1; un-weighted
        # CE drives the model toward all-O predictions. Weight = inverse class
        # frequency, clipped to [w_min, w_max]. Defaults preserve v13 behavior.
        # Tighten via LAYOUTLM_CLASS_WEIGHT_MIN / _MAX to reduce over-prediction
        # of rare classes at the cost of accepting more O bias.
        def _read_float_env(name: str, default: float) -> float:
            raw = os.getenv(name)
            if raw is None:
                return default
            try:
                return float(raw)
            except ValueError:
                print(
                    f"[trainer] WARN: {name}={raw!r} is not a valid float; "
                    f"falling back to default {default}"
                )
                return default

        w_min = _read_float_env("LAYOUTLM_CLASS_WEIGHT_MIN", 0.3)
        w_max = _read_float_env("LAYOUTLM_CLASS_WEIGHT_MAX", 5.0)
        product_weight = self.training_config.product_detail_loss_weight
        if product_weight <= 0:
            raise ValueError(
                "product_detail_loss_weight must be > 0; "
                f"got {product_weight}"
            )
        if not (0 < w_min <= w_max):
            raise ValueError(
                f"class weight clip range invalid: "
                f"min={w_min}, max={w_max} (require 0 < min <= max)"
            )
        torch_mod = self._torch
        n_classes = len(label_list)
        class_counts = [0] * n_classes
        for ex in datasets["train"]:
            for lid in ex["labels"]:
                if lid == -100:
                    continue
                if 0 <= lid < n_classes:
                    class_counts[lid] += 1
        total = sum(class_counts)
        weights = []
        for c in class_counts:
            if c == 0:
                weights.append(1.0)
            else:
                w = total / (n_classes * c)
                weights.append(max(w_min, min(w, w_max)))
        if product_weight != 1.0:
            product_cap = w_max * max(product_weight, 1.0)
            product_labels = {
                "PRODUCT_NAME",
                "QUANTITY",
                "UNIT_PRICE",
                "LINE_TOTAL",
            }
            for idx, label in enumerate(label_list):
                base_label = label[2:] if label.startswith(("B-", "I-")) else label
                if base_label in product_labels:
                    weights[idx] = max(
                        w_min,
                        min(weights[idx] * product_weight, product_cap),
                    )
        class_weights_tensor = torch_mod.tensor(
            weights, dtype=torch_mod.float32
        )
        print(
            "[trainer] class weights: "
            + ", ".join(
                f"{label_list[i]}={weights[i]:.2f}" for i in range(n_classes)
            )
        )
        if product_weight != 1.0:
            print(
                "[trainer] product detail loss weight multiplier: "
                f"{product_weight:.3f} (effective product cap "
                f"{product_cap:.3f})"
            )

        TrainerBase = self._transformers.Trainer

        class WeightedTrainer(TrainerBase):  # type: ignore[misc, valid-type]
            _class_weights = class_weights_tensor

            def compute_loss(
                self,
                model,
                inputs,
                return_outputs=False,
                num_items_in_batch=None,
            ):
                labels = inputs.pop("labels")
                outputs = model(**inputs)
                logits = outputs.logits
                # When transformers Trainer is using gradient accumulation
                # (4.46+), it passes num_items_in_batch so per-microbatch loss
                # can be summed-then-divided rather than mean-reduced. Mirror
                # the standard ForTokenClassification loss pattern so GA > 1
                # weighs microbatches correctly regardless of -100 padding.
                use_sum_reduction = num_items_in_batch is not None
                loss_fct = torch_mod.nn.CrossEntropyLoss(
                    weight=self._class_weights.to(logits.device),
                    ignore_index=-100,
                    reduction="sum" if use_sum_reduction else "mean",
                )
                loss = loss_fct(
                    logits.view(-1, logits.size(-1)),
                    labels.view(-1),
                )
                if use_sum_reduction:
                    loss = loss / num_items_in_batch
                return (loss, outputs) if return_outputs else loss

        trainer = WeightedTrainer(
            model=model,
            args=training_args,
            train_dataset=datasets.get("train"),
            eval_dataset=datasets.get("validation"),
            processing_class=self.tokenizer,
            data_collator=collator,
            compute_metrics=compute_metrics,
            callbacks=callbacks or None,
        )

        # Resume only if a checkpoint exists in output_dir; otherwise start fresh
        checkpoints = glob(f"{output_dir}/checkpoint-*/")
        if checkpoints:
            latest = max(checkpoints, key=_checkpoint_step)
            trainer.train(resume_from_checkpoint=latest)
        else:
            trainer.train()
        # Optionally, add a completion status when JobStatus write path is stable

        # After training, sync model to S3 if configured
        if self.training_config.output_s3_path:
            self._sync_model_to_s3(output_dir, job_name)

        # After training, store epoch metrics snapshot to Dynamo
        try:
            from receipt_dynamo.data.shared_exceptions import (
                DynamoDBError,
                EntityError,
                OperationError,
            )

            with open(run_json_path, "r", encoding="utf-8") as f:
                _data = json.load(f)

            epoch_metrics = _data.get("epoch_metrics", [])

            # Calculate best epoch and early stopping info
            best_epoch = None
            best_f1 = None
            best_selection_epoch = None
            best_selection_metric_value = None
            early_stopping_triggered = False

            # Find best aggregate validation F1 for backwards-compatible
            # reporting, even when checkpoint selection uses a different metric.
            f1_with_epochs = _epoch_metric_values(epoch_metrics, "eval_f1")
            if f1_with_epochs:
                best_epoch, best_f1 = max(f1_with_epochs, key=lambda x: x[1])

            selection_metric_name = _checkpoint_metric_for_trainer(
                self.training_config.checkpoint_metric
            )
            selection_values = _epoch_metric_values(
                epoch_metrics, selection_metric_name
            )
            if selection_values:
                selector = max if _metric_greater_is_better(
                    selection_metric_name
                ) else min
                best_selection_epoch, best_selection_metric_value = selector(
                    selection_values,
                    key=lambda x: x[1],
                )

            if epoch_metrics:
                last_epoch = max(
                    (
                        m.get("epoch")
                        for m in epoch_metrics
                        if m.get("epoch") is not None
                    ),
                    default=None,
                )
                if (
                    last_epoch is not None
                    and best_selection_epoch is not None
                    and last_epoch < self.training_config.epochs - 1
                ):
                    epochs_since_best = last_epoch - best_selection_epoch
                    if epochs_since_best >= (
                        self.training_config.early_stopping_patience
                    ):
                        early_stopping_triggered = True

            summary_payload = {
                "type": "run_summary",
                "epoch_metrics": epoch_metrics,
                "checkpoint_metric": selection_metric_name,
            }

            # Add early stopping information if available
            if best_epoch is not None:
                summary_payload["best_epoch"] = float(best_epoch)
                summary_payload["best_f1"] = (
                    float(best_f1) if best_f1 is not None else None
                )
                summary_payload["early_stopping_triggered"] = (
                    early_stopping_triggered
                )
                if epoch_metrics:
                    last_epoch = max(
                        (
                            m.get("epoch")
                            for m in epoch_metrics
                            if m.get("epoch") is not None
                        ),
                        default=None,
                    )
                    if last_epoch is not None:
                        summary_payload["epochs_since_best"] = (
                            int(last_epoch - best_epoch)
                            if best_epoch is not None
                            else None
                        )
            if best_selection_epoch is not None:
                summary_payload["best_checkpoint_metric"] = (
                    selection_metric_name
                )
                summary_payload["best_checkpoint_metric_epoch"] = float(
                    best_selection_epoch
                )
                summary_payload["best_checkpoint_metric_value"] = (
                    float(best_selection_metric_value)
                    if best_selection_metric_value is not None
                    else None
                )

            # Extract checkpoint and training time information from trainer_state.json
            trainer_state_path = os.path.join(output_dir, "trainer_state.json")
            if not os.path.exists(trainer_state_path):
                # HuggingFace Trainer saves trainer_state.json inside
                # checkpoint subdirectories, not at the root output_dir.
                # Find the latest checkpoint's copy.
                checkpoint_states = sorted(
                    glob(os.path.join(output_dir, "checkpoint-*/trainer_state.json")),
                    key=_checkpoint_step,
                )
                if checkpoint_states:
                    trainer_state_path = checkpoint_states[-1]
            if os.path.exists(trainer_state_path):
                try:
                    with open(trainer_state_path, "r", encoding="utf-8") as f:
                        trainer_state = json.load(f)

                    # Best checkpoint path
                    best_checkpoint = trainer_state.get(
                        "best_model_checkpoint"
                    )
                    if best_checkpoint:
                        # Convert to S3 path if output_s3_path is configured
                        if self.training_config.output_s3_path:
                            s3_path = self.training_config.output_s3_path
                            if s3_path.startswith("s3://"):
                                parsed = urlparse(s3_path)
                                bucket = parsed.netloc
                                prefix = parsed.path.lstrip("/")
                                if not prefix.endswith("/"):
                                    prefix += "/"
                                run_prefix = f"{prefix}{job_name}/"
                            else:
                                bucket = s3_path
                                run_prefix = f"runs/{job_name}/"

                            # Best checkpoint is synced to best/ subdirectory
                            summary_payload["best_checkpoint_s3_path"] = (
                                f"s3://{bucket}/{run_prefix}best/"
                            )
                        else:
                            # Just store local path
                            summary_payload["best_checkpoint_path"] = (
                                best_checkpoint
                            )

                    # Training time metrics
                    train_runtime = trainer_state.get("train_runtime")
                    if train_runtime is not None:
                        summary_payload["train_runtime_seconds"] = float(
                            train_runtime
                        )

                    total_flos = trainer_state.get("total_flos")
                    if total_flos is not None:
                        summary_payload["total_flos"] = int(total_flos)

                    # Number of checkpoints saved (count checkpoint directories)
                    checkpoint_dirs = glob(
                        os.path.join(output_dir, "checkpoint-*/")
                    )
                    if checkpoint_dirs:
                        summary_payload["num_checkpoints"] = len(
                            checkpoint_dirs
                        )
                except (json.JSONDecodeError, KeyError, ValueError):
                    # Best-effort; ignore errors
                    pass

            summary_log = JobLog(
                job_id=job.job_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                log_level="INFO",
                message=json.dumps(summary_payload),
                source="receipt_layoutlm.trainer",
            )
            self.dynamo.add_job_log(summary_log)

            # Update Job with results and mark as succeeded
            job.status = "succeeded"
            job.results = {
                "best_f1": float(best_f1) if best_f1 is not None else None,
                "best_epoch": (
                    int(best_epoch) if best_epoch is not None else None
                ),
                "best_f1_epoch": (
                    int(best_epoch) if best_epoch is not None else None
                ),
                "checkpoint_metric": selection_metric_name,
                "best_checkpoint_metric_value": (
                    float(best_selection_metric_value)
                    if best_selection_metric_value is not None
                    else None
                ),
                "best_checkpoint_metric_epoch": (
                    int(best_selection_epoch)
                    if best_selection_epoch is not None
                    else None
                ),
                "early_stopping_triggered": early_stopping_triggered,
            }
            # Add training time metrics if available
            if "train_runtime_seconds" in summary_payload:
                job.results["train_runtime"] = summary_payload[
                    "train_runtime_seconds"
                ]
            if "total_flos" in summary_payload:
                job.results["total_flos"] = summary_payload["total_flos"]
            if "best_checkpoint_s3_path" in summary_payload:
                job.results["best_checkpoint_s3_path"] = summary_payload[
                    "best_checkpoint_s3_path"
                ]
            elif "best_checkpoint_path" in summary_payload:
                job.results["best_checkpoint_path"] = summary_payload[
                    "best_checkpoint_path"
                ]

            self.dynamo.update_job(job)
            print(
                f"Job {job.job_id} completed with status: {job.status}, best_f1: {job.results.get('best_f1')}"
            )

            # Queue CoreML export if auto-export is enabled
            if self.training_config.auto_export_coreml:
                self._queue_coreml_export(
                    job, quantize=self.training_config.coreml_quantize
                )

        except (
            DynamoDBError,
            EntityError,
            OperationError,
            FileNotFoundError,
            json.JSONDecodeError,
            ValueError,
        ) as e:
            print(f"Warning: Failed to update job completion status: {e}")

        return job.job_id

    def _initialize_category_aware_embeddings(
        self, model: Any, label2id: Dict[str, int]
    ) -> None:
        """Initialize label embeddings with category information.

        This method adjusts label embeddings to be closer to their category centroids,
        helping the model learn relationships between labels in the same category.

        Categories:
        - merchant: MERCHANT_NAME, PHONE_NUMBER, ADDRESS_LINE
        - transaction: DATE, TIME
        - lineItems: PRODUCT_NAME
        - totals: AMOUNT (merged from LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL)
        """
        # Define label-to-category mapping (matching UI grouping)
        # Handle merged labels: ADDRESS (from ADDRESS_LINE+PHONE_NUMBER), DATE (from DATE+TIME)
        LABEL_CATEGORIES = {
            "merchant": [
                "MERCHANT_NAME",
                "PHONE_NUMBER",
                "ADDRESS_LINE",
                "ADDRESS",
            ],
            "transaction": [
                "DATE",
                "TIME",
            ],  # DATE may be merged from DATE+TIME
            "lineItems": ["PRODUCT_NAME"],
            "totals": ["AMOUNT"],
        }

        # Build reverse mapping: label -> category
        label_to_category: Dict[str, str] = {}
        for category, labels in LABEL_CATEGORIES.items():
            for label in labels:
                label_to_category[label] = category

        # Get label embeddings from the classifier layer
        # v1: classifier.weight is [num_labels, hidden_size]
        # v3: classifier.out_proj.weight is [num_labels, hidden_size]
        if not hasattr(model, "classifier"):
            print(
                "⚠️  Model doesn't have classifier, skipping category-aware initialization"
            )
            return

        if hasattr(model.classifier, "out_proj"):
            label_embeddings = model.classifier.out_proj.weight.data
        elif hasattr(model.classifier, "weight"):
            label_embeddings = model.classifier.weight.data
        else:
            print(
                "⚠️  Model classifier has no weight or out_proj, skipping category-aware initialization"
            )
            return

        # Only process labels that exist in our label2id mapping
        available_labels = set(label2id.keys())
        category_labels: Dict[str, List[str]] = {}
        for category, labels in LABEL_CATEGORIES.items():
            category_labels[category] = [
                label
                for label in labels
                if label in available_labels and label != "O"
            ]

        # Calculate category centroids (average embedding for labels in each category)
        category_centroids: Dict[str, Any] = {}
        for category, labels in category_labels.items():
            if len(labels) == 0:
                continue

            # Get embeddings for labels in this category
            category_embs = []
            for label in labels:
                if label in label2id:
                    label_idx = label2id[label]
                    category_embs.append(label_embeddings[label_idx].clone())

            if len(category_embs) > 0:
                # Calculate centroid (mean of embeddings)
                category_centroids[category] = self._torch.stack(
                    category_embs
                ).mean(dim=0)

        # Adjust label embeddings to be closer to their category centroids
        # Use a weighted average: 70% original embedding, 30% category centroid
        # This preserves label-specific information while encouraging category relationships
        adjusted_count = 0
        for label, category in label_to_category.items():
            if label not in label2id or label == "O":
                continue
            if category not in category_centroids:
                continue

            label_idx = label2id[label]
            original_emb = label_embeddings[label_idx].clone()
            centroid = category_centroids[category]

            # Weighted average: 70% original, 30% centroid
            # This moves the embedding closer to the category centroid while preserving
            # label-specific information learned from pre-training
            adjusted_emb = 0.7 * original_emb + 0.3 * centroid
            label_embeddings[label_idx] = adjusted_emb
            adjusted_count += 1

        if adjusted_count > 0:
            print(
                f"✅ Initialized category-aware embeddings for {adjusted_count} labels "
                f"across {len(category_centroids)} categories"
            )
        else:
            print("⚠️  No labels found for category-aware initialization")

    def _sync_model_to_s3(self, output_dir: str, job_name: str) -> None:
        """Sync trained model to S3 after training completes.

        Syncs:
        1. Full run directory to s3://bucket/runs/{job_name}/
        2. Best checkpoint to s3://bucket/runs/{job_name}/best/
        """
        s3_path = self.training_config.output_s3_path
        if not s3_path:
            return

        # Parse S3 path - can be bucket name or full s3:// URI
        if s3_path.startswith("s3://"):
            # Full URI provided
            parsed = urlparse(s3_path)
            bucket = parsed.netloc
            prefix = parsed.path.lstrip("/")
            if not prefix.endswith("/"):
                prefix += "/"
            # Use provided prefix + job_name
            run_prefix = f"{prefix}{job_name}/"
        else:
            # Just bucket name - use standard structure
            bucket = s3_path
            run_prefix = f"runs/{job_name}/"

        try:
            # Try using boto3 first (faster, more reliable)
            try:
                boto3 = importlib.import_module("boto3")
                s3_client = boto3.client("s3")

                # Sync full run directory
                print(
                    f"📤 Syncing run directory to s3://{bucket}/{run_prefix}"
                )
                for root, _dirs, files in os.walk(output_dir):
                    for file in files:
                        local_path = os.path.join(root, file)
                        # Get relative path from output_dir
                        rel_path = os.path.relpath(local_path, output_dir)
                        s3_key = f"{run_prefix}{rel_path}"
                        s3_client.upload_file(local_path, bucket, s3_key)

                # Find and sync best checkpoint
                trainer_state_path = os.path.join(
                    output_dir, "trainer_state.json"
                )
                if not os.path.exists(trainer_state_path):
                    checkpoint_states = sorted(
                        glob(os.path.join(output_dir, "checkpoint-*/trainer_state.json")),
                        key=_checkpoint_step,
                    )
                    if checkpoint_states:
                        trainer_state_path = checkpoint_states[-1]
                if os.path.exists(trainer_state_path):
                    with open(trainer_state_path, "r") as f:
                        trainer_state = json.load(f)
                    best_checkpoint = trainer_state.get(
                        "best_model_checkpoint"
                    )
                    if best_checkpoint:
                        # Handle relative paths (relative to output_dir)
                        if not os.path.isabs(best_checkpoint):
                            best_checkpoint = os.path.join(
                                output_dir, best_checkpoint
                            )
                        if os.path.exists(best_checkpoint):
                            print(
                                f"📤 Syncing best checkpoint to s3://{bucket}/{run_prefix}best/"
                            )
                            best_prefix = f"{run_prefix}best/"
                            for root, _dirs, files in os.walk(best_checkpoint):
                                for file in files:
                                    local_path = os.path.join(root, file)
                                    rel_path = os.path.relpath(
                                        local_path, best_checkpoint
                                    )
                                    s3_key = f"{best_prefix}{rel_path}"
                                    s3_client.upload_file(
                                        local_path, bucket, s3_key
                                    )
                            print(
                                f"✅ Model synced to s3://{bucket}/{run_prefix}"
                            )
                        else:
                            print(
                                f"⚠️  Best checkpoint path not found: {best_checkpoint}"
                            )
                    else:
                        print(
                            f"⚠️  No best checkpoint recorded in trainer_state.json"
                        )
                else:
                    print(
                        f"⚠️  trainer_state.json not found, synced full run directory only"
                    )

            except ModuleNotFoundError:
                # Fallback to AWS CLI
                print(
                    f"📤 Syncing run directory to s3://{bucket}/{run_prefix} (using AWS CLI)"
                )
                result = subprocess.run(
                    [
                        "aws",
                        "s3",
                        "sync",
                        output_dir,
                        f"s3://{bucket}/{run_prefix}",
                    ],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    print(f"⚠️  Failed to sync to S3: {result.stderr}")
                    return

                # Find and sync best checkpoint
                trainer_state_path = os.path.join(
                    output_dir, "trainer_state.json"
                )
                if not os.path.exists(trainer_state_path):
                    checkpoint_states = sorted(
                        glob(os.path.join(output_dir, "checkpoint-*/trainer_state.json")),
                        key=_checkpoint_step,
                    )
                    if checkpoint_states:
                        trainer_state_path = checkpoint_states[-1]
                if os.path.exists(trainer_state_path):
                    with open(trainer_state_path, "r") as f:
                        trainer_state = json.load(f)
                    best_checkpoint = trainer_state.get(
                        "best_model_checkpoint"
                    )
                    if best_checkpoint:
                        # Handle relative paths (relative to output_dir)
                        if not os.path.isabs(best_checkpoint):
                            best_checkpoint = os.path.join(
                                output_dir, best_checkpoint
                            )
                        if os.path.exists(best_checkpoint):
                            print(
                                f"📤 Syncing best checkpoint to s3://{bucket}/{run_prefix}best/"
                            )
                            subprocess.run(
                                [
                                    "aws",
                                    "s3",
                                    "sync",
                                    best_checkpoint,
                                    f"s3://{bucket}/{run_prefix}best/",
                                ],
                                capture_output=True,
                            )
                            print(
                                f"✅ Model synced to s3://{bucket}/{run_prefix}"
                            )
                        else:
                            print(
                                f"⚠️  Best checkpoint path not found: {best_checkpoint}"
                            )
                    else:
                        print(
                            f"⚠️  No best checkpoint recorded in trainer_state.json"
                        )
        except Exception as e:
            # Don't fail training if S3 sync fails
            print(f"⚠️  Failed to sync model to S3: {e}")
            print(
                f"   You can manually sync with: aws s3 sync {output_dir} s3://{bucket}/{run_prefix}"
            )

    def _queue_coreml_export(
        self,
        job: Job,
        quantize: Optional[str] = "float16",
    ) -> Optional[str]:
        """Queue a CoreML export job after training completes.

        This sends a message to the CoreML export job queue, which will be
        processed by a macOS worker (since coremltools only runs on macOS).

        Args:
            job: The completed training Job
            quantize: Quantization mode ("float16", "int8", "int4", or None)

        Returns:
            The export_id if queued successfully, None otherwise
        """
        # Get the model S3 URI from the job
        model_s3_uri = job.best_dir_uri()
        if not model_s3_uri:
            print(
                "⚠️  No best checkpoint S3 path found, skipping CoreML export"
            )
            return None

        # Get queue URL from environment
        queue_url = os.environ.get("COREML_EXPORT_JOB_QUEUE_URL")
        if not queue_url:
            print(
                "⚠️  COREML_EXPORT_JOB_QUEUE_URL not set, skipping CoreML export"
            )
            return None

        # Generate export ID
        export_id = str(uuid.uuid4())

        # Build output S3 prefix
        bucket = job.storage_bucket()
        if bucket:
            output_s3_prefix = f"s3://{bucket}/coreml/{job.name}/"
        else:
            print("⚠️  No S3 bucket configured, skipping CoreML export")
            return None

        try:
            import boto3

            # Create export job record in DynamoDB
            export_job = CoreMLExportJob(
                export_id=export_id,
                job_id=job.job_id,
                model_s3_uri=model_s3_uri,
                created_at=datetime.now(timezone.utc),
                status=CoreMLExportStatus.PENDING.value,
                quantize=quantize,
                output_s3_prefix=output_s3_prefix,
            )
            self.dynamo.add_coreml_export_job(export_job)

            # Send message to queue
            sqs = boto3.client("sqs", region_name=self.data_config.aws_region)
            message = {
                "export_id": export_id,
                "job_id": job.job_id,
                "model_s3_uri": model_s3_uri,
                "quantize": quantize,
                "output_s3_prefix": output_s3_prefix,
            }
            sqs.send_message(
                QueueUrl=queue_url,
                MessageBody=json.dumps(message),
            )

            print(f"✅ CoreML export queued: {export_id}")
            print(f"   Model: {model_s3_uri}")
            print(f"   Output: {output_s3_prefix}")
            return export_id

        except Exception as e:
            # Don't fail training if export queueing fails
            print(f"⚠️  Failed to queue CoreML export: {e}")
            return None
