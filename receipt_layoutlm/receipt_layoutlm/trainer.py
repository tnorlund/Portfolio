from dataclasses import asdict
from typing import Dict, List
import inspect
from functools import partial

import importlib

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
            # Placeholder: report token-level accuracy by exact match
            logits, labels = eval_pred
            preds = logits.argmax(-1)
            correct = (preds == labels).astype(float)
            acc = (
                correct.mean().item()
                if hasattr(correct, "mean")
                else float(correct.mean())
            )
            # Report to DynamoDB
            try:
                metric = JobMetric(
                    job_id=job.job_id,
                    metric_name="val_token_acc",
                    value=float(acc),
                    timestamp=datetime.now().isoformat(),
                    unit="ratio",
                )
                self.dynamo.add_job_metric(metric)
            except Exception:
                pass
            return {"accuracy": acc}

        trainer = self._transformers.Trainer(
            model=model,
            args=training_args,
            train_dataset=datasets.get("train"),
            eval_dataset=datasets.get("validation"),
            tokenizer=self.tokenizer,
            data_collator=collator,
            compute_metrics=compute_metrics,
        )

        try:
            trainer.train()
            # Optionally, add a completion status when JobStatus write path is stable
        except Exception as e:
            raise

        return job.job_id
