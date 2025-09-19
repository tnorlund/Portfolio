from dataclasses import asdict
from typing import Dict, List
import inspect
from functools import partial

import importlib
from glob import glob

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities.job import Job
from receipt_dynamo.entities.job_metric import JobMetric

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
        datasets = load_datasets(self.dynamo)
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
            for i in range(seq_len):
                wid = word_ids[i]
                if wid is None:
                    labels.append(-100)
                    bboxes.append([0, 0, 0, 0])
                else:
                    lbl = example["ner_tags"][wid]
                    labels.append(label2id.get(lbl, 0))
                    bboxes.append(example["bboxes"][wid])
            encoding["labels"] = labels
            encoding["bbox"] = bboxes
            return encoding

        preprocess = partial(
            _preprocess,
            tokenizer=self.tokenizer,
            label2id=label2id,
            max_len=self.data_config.max_seq_length,
        )

        datasets = datasets.map(  # type: ignore[attr-defined]
            preprocess,
            remove_columns=["tokens", "bboxes", "ner_tags"],
        )

        # Version-compatible TrainingArguments
        args_kwargs = dict(
            output_dir="/tmp/receipt_layoutlm",
            per_device_train_batch_size=self.training_config.batch_size,
            per_device_eval_batch_size=self.training_config.batch_size,
            learning_rate=self.training_config.learning_rate,
            weight_decay=self.training_config.weight_decay,
            num_train_epochs=self.training_config.epochs,
            warmup_ratio=self.training_config.warmup_ratio,
            max_grad_norm=self.training_config.max_grad_norm,
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
        # Early stopping if available
        if "load_best_model_at_end" in ta_params:
            args_kwargs["load_best_model_at_end"] = True
        callbacks = []
        try:
            es_cls = getattr(self._transformers, "EarlyStoppingCallback")
            callbacks.append(es_cls(early_stopping_patience=2))
        except AttributeError:
            pass
        if "remove_unused_columns" in ta_params:
            args_kwargs["remove_unused_columns"] = False

        training_args = ta_cls(**args_kwargs)

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
                try:
                    metric = JobMetric(
                        job_id=job.job_id,
                        metric_name="val_f1",
                        value=f1,
                        timestamp=datetime.now().isoformat(),
                        unit="ratio",
                    )
                    self.dynamo.add_job_metric(metric)
                except Exception:
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
        output_dir = args_kwargs["output_dir"]
        checkpoints = sorted(glob(f"{output_dir}/checkpoint-*/"))
        if checkpoints:
            trainer.train(resume_from_checkpoint=checkpoints[-1])
        else:
            trainer.train()
        # Optionally, add a completion status when JobStatus write path is stable

        return job.job_id
