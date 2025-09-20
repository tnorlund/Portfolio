from dataclasses import asdict
from typing import Dict, List
import json
import inspect
from functools import partial

import importlib
import os
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
            for tag in seq:
                tags.add(tag)
        # Ensure 'O' exists and index 0 is allowed by Trainer
        labels = sorted(tags - {"O"})
        return ["O"] + labels

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
            args_kwargs["metric_for_best_model"] = "f1"
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
        if "torch_compile" in ta_params:
            enable_compile = False
            try:
                importlib.import_module("triton")
                importlib.import_module("torch._inductor")
                enable_compile = True
            except (ModuleNotFoundError, ImportError):
                enable_compile = False
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

        # Per-epoch metric logging to run.json
        try:
            TrainerCallback = getattr(self._transformers, "TrainerCallback")

            class _MetricLoggerCallback(TrainerCallback):  # type: ignore
                def __init__(self, run_path: str) -> None:
                    self.run_path = run_path

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
                    entry = {
                        "epoch": (
                            float(state.epoch)
                            if getattr(state, "epoch", None)
                            else None
                        ),
                        "global_step": int(getattr(state, "global_step", 0)),
                    }
                    if isinstance(metrics, dict):
                        entry.update(
                            {
                                k: float(v)
                                for k, v in metrics.items()
                                if isinstance(v, (int, float))
                            }
                        )
                    epoch_metrics.append(entry)
                    data["epoch_metrics"] = epoch_metrics
                    with open(self.run_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)

            callbacks.append(
                _MetricLoggerCallback(os.path.join(output_dir, "run.json"))
            )
        except AttributeError:
            # Older transformers versions may lack TrainerCallback
            pass
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
            except ModuleNotFoundError:
                # Fallback to token accuracy if seqeval unavailable
                seqeval = None

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
            if seqeval:
                f1 = float(f1_fn(y_true, y_pred))
                prec = float(precision_fn(y_true, y_pred))
                rec = float(recall_fn(y_true, y_pred))
                metrics = {"f1": f1, "precision": prec, "recall": rec}
                # Report to DynamoDB (F1)
                metric = JobMetric(
                    job_id=job.job_id,
                    metric_name="val_f1",
                    value=f1,
                    timestamp=datetime.now().isoformat(),
                    unit="ratio",
                )
                # Best-effort write; ignore errors from downstream service issues
                from receipt_dynamo.data.shared_exceptions import (
                    DynamoDBError,
                    EntityError,
                    OperationError,
                )

                try:
                    self.dynamo.add_job_metric(metric)
                except (DynamoDBError, EntityError, OperationError):
                    pass
            else:
                # Token accuracy fallback
                import numpy as _np

                correct = (preds == labels).astype(float)
                acc = float(_np.mean(correct))
                metrics = {"accuracy": acc}
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
        checkpoints = sorted(glob(f"{output_dir}/checkpoint-*/"))
        if checkpoints:
            trainer.train(resume_from_checkpoint=checkpoints[-1])
        else:
            trainer.train()
        # Optionally, add a completion status when JobStatus write path is stable

        # After training, store epoch metrics snapshot to Dynamo
        try:
            from receipt_dynamo.data.shared_exceptions import (
                DynamoDBError,
                EntityError,
                OperationError,
            )

            with open(run_json_path, "r", encoding="utf-8") as f:
                _data = json.load(f)
            summary_log = JobLog(
                job_id=job.job_id,
                timestamp=datetime.now().isoformat(),
                log_level="INFO",
                message=json.dumps(
                    {
                        "type": "run_summary",
                        "epoch_metrics": _data.get("epoch_metrics", []),
                    }
                ),
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
