"""Main trainer class for Receipt Trainer."""

import os
import json
import tempfile
import logging
import torch
from pathlib import Path
from typing import Optional, Dict, Any, List
from datasets import Dataset, DatasetDict, load_dataset, Value
from transformers import (
    AutoModelForTokenClassification,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
)

def _load_sroie_data(self):
    """Load and prepare the SROIE dataset."""
    self.logger.info("Loading SROIE dataset...")

    # Define numeric to string mapping for SROIE labels
    sroie_idx_to_label = {
        0: "O",
        1: "B-COMPANY",
        2: "I-COMPANY",
        3: "B-DATE",
        4: "I-DATE",
        5: "B-ADDRESS",
        6: "I-ADDRESS",
        7: "B-TOTAL",
        8: "I-TOTAL",
    }

    # Define tag mapping from SROIE to our format
    sroie_to_our_format = {
        "B-COMPANY": "B-store_name",
        "I-COMPANY": "I-store_name",
        "B-ADDRESS": "B-address",
        "I-ADDRESS": "I-address",
        "B-DATE": "B-date",
        "I-DATE": "I-date",
        "B-TOTAL": "B-total_amount",
        "I-TOTAL": "I-total_amount",
        "O": "O",
    }

    dataset = load_dataset("darentang/sroie")
    train_data = {}
    test_data = {}

    def convert_example(
        example: Dict[str, Any], idx: int, split: str
    ) -> Dict[str, Any]:
        # Convert numeric labels to string labels, then to our format
        labels = [
            sroie_to_our_format[sroie_idx_to_label[label]]
            for label in example["ner_tags"]
        ]

        # Scale coordinates to 0-1000
        boxes = example["bboxes"]
        max_x = max(max(box[0], box[2]) for box in boxes)
        max_y = max(max(box[1], box[3]) for box in boxes)
        scale = 1000 / max(max_x, max_y)

        normalized_boxes = [
            [
                int(box[0] * scale),
                int(box[1] * scale),
                int(box[2] * scale),
                int(box[3] * scale),
            ]
            for box in boxes
        ]

        return {
            "words": example["words"],
            "bboxes": normalized_boxes,
            "labels": labels,
            "image_id": f"sroie_{split}_{idx}",
            "width": max_x,
            "height": max_y,
        }

    # Convert train split
    for idx, example in enumerate(dataset["train"]):
        train_data[f"sroie_train_{idx}"] = convert_example(example, idx, "train")

    # Convert test split
    for idx, example in enumerate(dataset["test"]):
        test_data[f"sroie_test_{idx}"] = convert_example(example, idx, "test")

    self.logger.info(
        f"Loaded {len(train_data)} training and {len(test_data)} test examples from SROIE"
    )

    # Create datasets
    train_dataset = Dataset.from_list(train_data.values(), features={
        "words": [Value("string")],
        "bbox": [[Value("int64")] * 4],
        "labels": [Value("string")],
        "image_id": Value("string"),
    })
    val_dataset = Dataset.from_list(test_data.values(), features={
        "words": [Value("string")],
        "bbox": [[Value("int64")] * 4],
        "labels": [Value("string")],
        "image_id": Value("string"),
    })

    # Print statistics
    self._print_dataset_statistics(train_dataset, "Train")
    self._print_dataset_statistics(val_dataset, "Validation")

    return DatasetDict({"train": train_dataset, "validation": val_dataset})
