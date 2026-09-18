"""Shared packaging of tokenizer / config / label sidecars for model bundles."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Union


def write_export_sidecars(
    *,
    checkpoint_path: Path,
    output_path: Path,
    tokenizer: Any,
    config: Any,
    id2label: Mapping[Union[int, str], str],
    label2id: Mapping[str, Union[int, str]],
    num_labels: int,
    model_version: str = "v1",
) -> None:
    """Write vocab/config/label_map/tokenizer sidecars into an export bundle.

    Matches the historical Core ML exporter layout so Swift can load either
    backend from the same metadata files.
    """
    output_path.mkdir(parents=True, exist_ok=True)

    # NOTE: v3 uses RoBERTa BPE tokenizer, not BERT WordPiece. The Swift
    # BertTokenizer.swift is not compatible with v3 vocab. A v3-specific
    # Swift tokenizer is needed before v3 inference works end-to-end.
    vocab_src = checkpoint_path / "vocab.txt"
    vocab_dst = output_path / "vocab.txt"
    if model_version == "v3":
        print(
            "WARNING: v3 uses RoBERTa BPE tokenizer — vocab.txt is not "
            "compatible with Swift BertTokenizer"
        )
        print(
            "         v3 Swift inference requires a BPE tokenizer "
            "implementation (follow-up PR)"
        )
        tokenizer.save_pretrained(str(output_path))
        print(f"Saved v3 tokenizer files to {output_path}")
    elif vocab_src.exists():
        shutil.copy(vocab_src, vocab_dst)
        print(f"Copied vocab.txt to {vocab_dst}")
    else:
        vocab = tokenizer.get_vocab()
        sorted_tokens = sorted(vocab.items(), key=lambda kv: kv[1])
        with open(vocab_dst, "w", encoding="utf-8") as f:
            for token, _ in sorted_tokens:
                f.write(token + "\n")
        print(f"Wrote vocab.txt to {vocab_dst} ({len(sorted_tokens)} tokens)")

    # Normalize id2label keys to ints for stable sorting.
    id2label_int: dict[int, str] = {
        int(k): str(v) for k, v in id2label.items()
    }
    sorted_ids = sorted(id2label_int.keys())

    config_src = checkpoint_path / "config.json"
    config_dst = output_path / "config.json"
    if config_src.exists():
        with open(config_src, "r", encoding="utf-8") as f:
            config_data: MutableMapping[str, Any] = json.load(f)
        if "num_labels" not in config_data:
            config_data["num_labels"] = num_labels
            print(f"Added num_labels={num_labels} to config.json")
        with open(config_dst, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
            f.write("\n")
        print(f"Saved config.json to {config_dst}")
    else:
        with open(config_dst, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id2label": {str(k): id2label_int[k] for k in sorted_ids},
                    "label2id": dict(label2id),
                    "num_labels": num_labels,
                    "max_position_embeddings": config.max_position_embeddings,
                    "vocab_size": config.vocab_size,
                },
                f,
                indent=2,
            )
            f.write("\n")
        print(f"Saved config.json to {config_dst}")

    label_map_path = output_path / "label_map.json"
    with open(label_map_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "id2label": {str(k): id2label_int[k] for k in sorted_ids},
                "label2id": dict(label2id),
                "labels": [id2label_int[k] for k in sorted_ids],
            },
            f,
            indent=2,
        )
    print(f"Saved label_map.json to {label_map_path}")

    tokenizer_config_src = checkpoint_path / "tokenizer_config.json"
    tokenizer_config_dst = output_path / "tokenizer_config.json"
    if tokenizer_config_src.exists():
        shutil.copy(tokenizer_config_src, tokenizer_config_dst)


def write_model_identity_sidecar(
    bundle_path: Path,
    identity: Mapping[str, Any],
) -> Path:
    """Write optional ``model_identity.json`` into a local export bundle."""
    path = bundle_path / "model_identity.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(identity), f, indent=2)
        f.write("\n")
    return path
