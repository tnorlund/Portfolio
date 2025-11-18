from dataclasses import asdict
from typing import Any, Dict, List
import json
import inspect
from functools import partial
from urllib.parse import urlparse

import importlib
import os
import subprocess
from glob import glob

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.job import Job
from receipt_dynamo.entities.job_metric import JobMetric
from receipt_dynamo.entities.job_log import JobLog

from .config import DataConfig, TrainingConfig
from .data_loader import load_datasets


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

        self.tokenizer = transformers.LayoutLMTokenizerFast.from_pretrained(
            self.training_config.pretrained_model_name
        )

    def _label_list(self, dataset) -> List[str]:
        tags = set()
        # dataset["ner_tags"] is a list of lists (per-line sequences)
        for seq in dataset["ner_tags"]:
            tags.update(seq)
        # Ensure 'O' exists and index 0 is allowed by Trainer
        labels = sorted(tags - {"O"})
        return ["O", *labels]

    def train(self, job_name: str, created_by: str = "system") -> str:
        # Derive output directory early for run logging
        output_dir = f"/tmp/receipt_layoutlm/{job_name}"
        # Best-effort create output directory
        os.makedirs(output_dir, exist_ok=True)
        # Pass allowed labels via env so data_loader can collapse others to O
        if self.data_config.allowed_labels:
            os.environ["LAYOUTLM_ALLOWED_LABELS"] = ",".join(
                [l.upper() for l in self.data_config.allowed_labels]
            )
        else:
            os.environ.pop("LAYOUTLM_ALLOWED_LABELS", None)

        # Merge currency-like amounts if requested
        if getattr(self.data_config, "merge_amounts", False):
            os.environ["LAYOUTLM_MERGE_AMOUNTS"] = "1"
        else:
            os.environ.pop("LAYOUTLM_MERGE_AMOUNTS", None)

        # Allow loading a prebuilt tokenized dataset snapshot
        ds_mod = importlib.import_module("datasets")
        if self.data_config.dataset_snapshot_load:
            try:
                datasets = ds_mod.load_from_disk(
                    self.data_config.dataset_snapshot_load
                )
            except (FileNotFoundError, OSError, ValueError):
                # Fallback to normal path if load fails
                datasets = load_datasets(self.dynamo)
        else:
            datasets = load_datasets(self.dynamo)

        # Compute dataset counts prior to tokenization for run logging
        def _count_labels(dataset) -> Dict[str, int]:
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

        dataset_counts: Dict[str, Dict[str, int]] = {}
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

        model = (
            self._transformers.LayoutLMForTokenClassification.from_pretrained(
                self.training_config.pretrained_model_name,
                num_labels=len(label_list),
                id2label=id2label,
                label2id=label2id,
            )
        )

        # Initialize category-aware label embeddings if enabled
        self._initialize_category_aware_embeddings(model, label2id)

        collator = self._transformers.DataCollatorForTokenClassification(
            tokenizer=self.tokenizer
        )

        # Prepare features: tokenize and align labels/bboxes
        def _preprocess(example, tokenizer, label2id, max_len: int):
            encoding = tokenizer(
                example["tokens"],
                is_split_into_words=True,
                truncation=True,
                padding="max_length",
                max_length=max_len,
                return_attention_mask=True,
            )
            word_ids = encoding.word_ids()
            seq_len = len(encoding["input_ids"])  # includes padding/specials
            labels = []
            bboxes = []
            prev_word_id = None
            for i in range(seq_len):
                wid = word_ids[i]
                if wid is None:
                    labels.append(-100)
                    bboxes.append([0, 0, 0, 0])
                else:
                    # Supervise only the first subtoken of each word; others = -100
                    if wid != prev_word_id:
                        lbl = example["ner_tags"][wid]
                        labels.append(label2id.get(lbl, 0))
                    else:
                        labels.append(-100)
                    bboxes.append(example["bboxes"][wid])
                    prev_word_id = wid
            encoding["labels"] = labels
            encoding["bbox"] = bboxes
            return encoding

        preprocess = partial(
            _preprocess,
            tokenizer=self.tokenizer,
            label2id=label2id,
            max_len=self.data_config.max_seq_length,
        )

        # Parallelize preprocessing across CPU cores
        num_proc = max(1, min(os.cpu_count() or 1, 8))
        datasets = datasets.map(  # type: ignore[attr-defined]
            preprocess,
            remove_columns=["tokens", "bboxes", "ner_tags"],
            num_proc=num_proc,
            desc="Tokenize+align",
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
        if "evaluation_strategy" in ta_params:
            args_kwargs["evaluation_strategy"] = "epoch"
        elif "eval_strategy" in ta_params:
            args_kwargs["eval_strategy"] = "epoch"
        if "save_strategy" in ta_params:
            args_kwargs["save_strategy"] = "epoch"
        if "load_best_model_at_end" in ta_params:
            args_kwargs["load_best_model_at_end"] = True
        if "metric_for_best_model" in ta_params:
            # Check if seqeval is available to determine which metric to use
            try:
                importlib.import_module("seqeval.metrics")
                args_kwargs["metric_for_best_model"] = "eval_f1"
            except ModuleNotFoundError:
                # Fallback to accuracy if seqeval not available
                args_kwargs["metric_for_best_model"] = "eval_accuracy"
        if "save_total_limit" in ta_params:
            args_kwargs["save_total_limit"] = 1
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

        # Write initial run.json (configs + dataset counts)
        run_json_path = os.path.join(output_dir, "run.json")
        run_payload = {
            "job_name": job_name,
            "training_config": asdict(self.training_config),
            "data_config": asdict(self.data_config),
            "label_list": label_list,
            "num_labels": len(label_list),
            "dataset_counts": dataset_counts,
            "epoch_metrics": [],
        }
        with open(run_json_path, "w", encoding="utf-8") as f:
            json.dump(run_payload, f, indent=2)

        import uuid
        from datetime import datetime

        job = Job(
            job_id=str(uuid.uuid4()),
            name=job_name,
            description="LayoutLM receipt token classification",
            created_at=datetime.now().isoformat(),
            created_by=created_by,
            status="pending",
            priority="medium",
            job_config={
                "model": "layoutlm",
                **asdict(self.training_config),
                **asdict(self.data_config),
            },
            estimated_duration=None,
            tags={},
        )
        self.dynamo.add_job(job)

        # Per-epoch metric logging to run.json (created after job so we have job_id)
        try:
            TrainerCallback = getattr(self._transformers, "TrainerCallback")

            class _MetricLoggerCallback(TrainerCallback):  # type: ignore
                def __init__(self, run_path: str, job_id: str, dynamo_client: Any) -> None:
                    self.run_path = run_path
                    self.job_id = job_id
                    self.dynamo = dynamo_client

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
                    if isinstance(metrics, dict):
                        # Add scalar metrics
                        entry.update(
                            {
                                k: float(v)
                                for k, v in metrics.items()
                                if isinstance(v, (int, float))
                            }
                        )
                        # Add per-label metrics if available (nested dict)
                        if "per_label_metrics" in metrics:
                            entry["per_label_metrics"] = metrics["per_label_metrics"]

                        # Store F1 metric to DynamoDB with epoch if available
                        if "eval_f1" in metrics or "f1" in metrics:
                            f1_value = metrics.get("eval_f1") or metrics.get("f1")
                            if f1_value is not None:
                                try:
                                    from receipt_dynamo.data.shared_exceptions import (
                                        DynamoDBError,
                                        EntityError,
                                        OperationError,
                                    )
                                    metric = JobMetric(
                                        job_id=self.job_id,
                                        metric_name="val_f1",
                                        value=float(f1_value),
                                        timestamp=datetime.now().isoformat(),
                                        unit="ratio",
                                        epoch=int(current_epoch) if current_epoch is not None else None,
                                        step=int(getattr(state, "global_step", 0)),
                                    )
                                    self.dynamo.add_job_metric(metric)
                                except (DynamoDBError, EntityError, OperationError):
                                    # Best-effort write; ignore errors
                                    pass

                    # Add training loss from log_history if available
                    if state and hasattr(state, "log_history") and state.log_history:
                        # Get the most recent training step log entry
                        train_logs = [log for log in state.log_history if "loss" in log and "eval_loss" not in log]
                        if train_logs:
                            latest_train = train_logs[-1]
                            if "loss" in latest_train:
                                entry["train_loss"] = float(latest_train["loss"])
                            if "learning_rate" in latest_train:
                                entry["learning_rate"] = float(latest_train["learning_rate"])

                    epoch_metrics.append(entry)
                    data["epoch_metrics"] = epoch_metrics
                    with open(self.run_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)

            callbacks.append(
                _MetricLoggerCallback(
                    os.path.join(output_dir, "run.json"),
                    job.job_id,
                    self.dynamo,
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
                timestamp=datetime.now().isoformat(),
                log_level="INFO",
                message=json.dumps(
                    {"type": "run_config", "data": run_payload}
                ),
                source="receipt_layoutlm.trainer",
            )
            self.dynamo.add_job_log(cfg_log)
        except (DynamoDBError, EntityError, OperationError, ValueError):
            pass

        def compute_metrics(eval_pred):
            # seqeval F1 at entity-level (BIO tags)
            try:
                seqeval = importlib.import_module("seqeval.metrics")
                f1_fn = getattr(seqeval, "f1_score")
                precision_fn = getattr(seqeval, "precision_score")
                recall_fn = getattr(seqeval, "recall_score")
                classification_report_fn = getattr(seqeval, "classification_report", None)
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

            metrics = {}
            per_label_metrics = {}
            if seqeval:
                f1 = float(f1_fn(y_true, y_pred))
                prec = float(precision_fn(y_true, y_pred))
                rec = float(recall_fn(y_true, y_pred))
                metrics = {"f1": f1, "precision": prec, "recall": rec}

                # Get per-label metrics if classification_report is available
                if classification_report_fn:
                    try:
                        # Get unique labels (excluding O for per-label metrics)
                        unique_labels = sorted(set([tag for seq in y_true + y_pred for tag in seq if tag != "O"]))
                        if unique_labels:
                            report = classification_report_fn(
                                y_true, y_pred, labels=unique_labels, output_dict=True, zero_division=0
                            )
                            # Extract per-label metrics
                            for label in unique_labels:
                                if label in report:
                                    per_label_metrics[label] = {
                                        "f1": float(report[label].get("f1-score", 0.0)),
                                        "precision": float(report[label].get("precision", 0.0)),
                                        "recall": float(report[label].get("recall", 0.0)),
                                        "support": int(report[label].get("support", 0)),
                                    }
                    except Exception:
                        # If classification_report fails, continue without per-label metrics
                        pass

                # Note: F1 metric is now stored in _MetricLoggerCallback.on_evaluate()
                # with epoch information, so we don't need to store it here
            else:
                # Token accuracy fallback
                import numpy as _np

                correct = (preds == labels).astype(float)
                acc = float(_np.mean(correct))
                metrics = {"accuracy": acc}

            # Add per-label metrics to return dict if available
            if per_label_metrics:
                metrics["per_label_metrics"] = per_label_metrics

            return metrics

        trainer = self._transformers.Trainer(
            model=model,
            args=training_args,
            train_dataset=datasets.get("train"),
            eval_dataset=datasets.get("validation"),
            tokenizer=self.tokenizer,
            data_collator=collator,
            compute_metrics=compute_metrics,
            callbacks=callbacks or None,
        )

        # Resume only if a checkpoint exists in output_dir; otherwise start fresh
        # Sort checkpoints numerically (not lexicographically) to handle checkpoint-10, checkpoint-100, etc.
        def _step_from_path(p: str) -> int:
            name = os.path.basename(os.path.dirname(p.rstrip("/")))
            try:
                return int(name.split("-")[-1])
            except ValueError:
                return -1

        checkpoints = glob(f"{output_dir}/checkpoint-*/")
        if checkpoints:
            latest = max(checkpoints, key=_step_from_path)
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
            early_stopping_triggered = False

            # Find best F1 score
            f1_metrics = [m for m in epoch_metrics if "eval_f1" in m or "f1" in m]
            if f1_metrics:
                # Get F1 value (try eval_f1 first, then f1)
                f1_with_epochs = [
                    (m.get("epoch"), m.get("eval_f1") or m.get("f1"))
                    for m in f1_metrics
                    if m.get("epoch") is not None and (m.get("eval_f1") is not None or m.get("f1") is not None)
                ]
                if f1_with_epochs:
                    best_epoch, best_f1 = max(f1_with_epochs, key=lambda x: x[1] if x[1] is not None else -1)

                    # Check if early stopping triggered
                    # If best epoch is not the last epoch, early stopping likely triggered
                    if epoch_metrics:
                        last_epoch = max(
                            (m.get("epoch") for m in epoch_metrics if m.get("epoch") is not None),
                            default=None
                        )
                        if last_epoch is not None and best_epoch is not None:
                            # Check if we stopped before max epochs
                            max_epochs = self.training_config.epochs
                            patience = self.training_config.early_stopping_patience
                            if last_epoch < max_epochs - 1:
                                # Check if best epoch was more than patience epochs ago
                                epochs_since_best = last_epoch - best_epoch
                                if epochs_since_best >= patience:
                                    early_stopping_triggered = True

            summary_payload = {
                "type": "run_summary",
                "epoch_metrics": epoch_metrics,
            }

            # Add early stopping information if available
            if best_epoch is not None:
                summary_payload["best_epoch"] = float(best_epoch)
                summary_payload["best_f1"] = float(best_f1) if best_f1 is not None else None
                summary_payload["early_stopping_triggered"] = early_stopping_triggered
                if epoch_metrics:
                    last_epoch = max(
                        (m.get("epoch") for m in epoch_metrics if m.get("epoch") is not None),
                        default=None
                    )
                    if last_epoch is not None:
                        summary_payload["epochs_since_best"] = int(last_epoch - best_epoch) if best_epoch is not None else None

            # Extract checkpoint and training time information from trainer_state.json
            trainer_state_path = os.path.join(output_dir, "trainer_state.json")
            if os.path.exists(trainer_state_path):
                try:
                    with open(trainer_state_path, "r", encoding="utf-8") as f:
                        trainer_state = json.load(f)

                    # Best checkpoint path
                    best_checkpoint = trainer_state.get("best_model_checkpoint")
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
                            summary_payload["best_checkpoint_s3_path"] = f"s3://{bucket}/{run_prefix}best/"
                        else:
                            # Just store local path
                            summary_payload["best_checkpoint_path"] = best_checkpoint

                    # Training time metrics
                    train_runtime = trainer_state.get("train_runtime")
                    if train_runtime is not None:
                        summary_payload["train_runtime_seconds"] = float(train_runtime)

                    total_flos = trainer_state.get("total_flos")
                    if total_flos is not None:
                        summary_payload["total_flos"] = int(total_flos)

                    # Number of checkpoints saved (count checkpoint directories)
                    checkpoint_dirs = glob(os.path.join(output_dir, "checkpoint-*/"))
                    if checkpoint_dirs:
                        summary_payload["num_checkpoints"] = len(checkpoint_dirs)
                except (json.JSONDecodeError, KeyError, ValueError):
                    # Best-effort; ignore errors
                    pass

            summary_log = JobLog(
                job_id=job.job_id,
                timestamp=datetime.now().isoformat(),
                log_level="INFO",
                message=json.dumps(summary_payload),
                source="receipt_layoutlm.trainer",
            )
            self.dynamo.add_job_log(summary_log)
        except (
            DynamoDBError,
            EntityError,
            OperationError,
            FileNotFoundError,
            json.JSONDecodeError,
            ValueError,
        ):
            pass

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
            "merchant": ["MERCHANT_NAME", "PHONE_NUMBER", "ADDRESS_LINE", "ADDRESS"],
            "transaction": ["DATE", "TIME"],  # DATE may be merged from DATE+TIME
            "lineItems": ["PRODUCT_NAME"],
            "totals": ["AMOUNT"],
        }

        # Build reverse mapping: label -> category
        label_to_category: Dict[str, str] = {}
        for category, labels in LABEL_CATEGORIES.items():
            for label in labels:
                label_to_category[label] = category

        # Get label embeddings from the classifier layer
        # The classifier is typically a linear layer: classifier.weight is [num_labels, hidden_size]
        if not hasattr(model, "classifier") or not hasattr(model.classifier, "weight"):
            print("⚠️  Model doesn't have classifier.weight, skipping category-aware initialization")
            return

        label_embeddings = model.classifier.weight.data  # Shape: [num_labels, hidden_size]

        # Only process labels that exist in our label2id mapping
        available_labels = set(label2id.keys())
        category_labels: Dict[str, List[str]] = {}
        for category, labels in LABEL_CATEGORIES.items():
            category_labels[category] = [
                label for label in labels
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
                category_centroids[category] = self._torch.stack(category_embs).mean(dim=0)

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
                print(f"📤 Syncing run directory to s3://{bucket}/{run_prefix}")
                for root, dirs, files in os.walk(output_dir):
                    for file in files:
                        local_path = os.path.join(root, file)
                        # Get relative path from output_dir
                        rel_path = os.path.relpath(local_path, output_dir)
                        s3_key = f"{run_prefix}{rel_path}"
                        s3_client.upload_file(local_path, bucket, s3_key)

                # Find and sync best checkpoint
                trainer_state_path = os.path.join(output_dir, "trainer_state.json")
                if os.path.exists(trainer_state_path):
                    with open(trainer_state_path, "r") as f:
                        trainer_state = json.load(f)
                    best_checkpoint = trainer_state.get("best_model_checkpoint")
                    if best_checkpoint:
                        # Handle relative paths (relative to output_dir)
                        if not os.path.isabs(best_checkpoint):
                            best_checkpoint = os.path.join(output_dir, best_checkpoint)
                        if os.path.exists(best_checkpoint):
                            print(f"📤 Syncing best checkpoint to s3://{bucket}/{run_prefix}best/")
                            best_prefix = f"{run_prefix}best/"
                            for root, dirs, files in os.walk(best_checkpoint):
                                for file in files:
                                    local_path = os.path.join(root, file)
                                    rel_path = os.path.relpath(local_path, best_checkpoint)
                                    s3_key = f"{best_prefix}{rel_path}"
                                    s3_client.upload_file(local_path, bucket, s3_key)
                            print(f"✅ Model synced to s3://{bucket}/{run_prefix}")
                        else:
                            print(f"⚠️  Best checkpoint path not found: {best_checkpoint}")
                    else:
                        print(f"⚠️  No best checkpoint recorded in trainer_state.json")
                else:
                    print(f"⚠️  trainer_state.json not found, synced full run directory only")

            except ModuleNotFoundError:
                # Fallback to AWS CLI
                print(f"📤 Syncing run directory to s3://{bucket}/{run_prefix} (using AWS CLI)")
                result = subprocess.run(
                    ["aws", "s3", "sync", output_dir, f"s3://{bucket}/{run_prefix}"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    print(f"⚠️  Failed to sync to S3: {result.stderr}")
                    return

                # Find and sync best checkpoint
                trainer_state_path = os.path.join(output_dir, "trainer_state.json")
                if os.path.exists(trainer_state_path):
                    with open(trainer_state_path, "r") as f:
                        trainer_state = json.load(f)
                    best_checkpoint = trainer_state.get("best_model_checkpoint")
                    if best_checkpoint:
                        # Handle relative paths (relative to output_dir)
                        if not os.path.isabs(best_checkpoint):
                            best_checkpoint = os.path.join(output_dir, best_checkpoint)
                        if os.path.exists(best_checkpoint):
                            print(f"📤 Syncing best checkpoint to s3://{bucket}/{run_prefix}best/")
                            subprocess.run(
                                ["aws", "s3", "sync", best_checkpoint, f"s3://{bucket}/{run_prefix}best/"],
                                capture_output=True,
                            )
                            print(f"✅ Model synced to s3://{bucket}/{run_prefix}")
                        else:
                            print(f"⚠️  Best checkpoint path not found: {best_checkpoint}")
                    else:
                        print(f"⚠️  No best checkpoint recorded in trainer_state.json")
        except Exception as e:
            # Don't fail training if S3 sync fails
            print(f"⚠️  Failed to sync model to S3: {e}")
            print(f"   You can manually sync with: aws s3 sync {output_dir} s3://{bucket}/{run_prefix}")
