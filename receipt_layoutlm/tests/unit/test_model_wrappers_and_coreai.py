"""Tests for shared LayoutLM wrappers and Core AI export helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from receipt_layoutlm.export_bundle import write_export_sidecars
from receipt_layoutlm.export_coreai import COREAI_SEQ_LENGTH, _sample_inputs
from receipt_layoutlm.model_wrappers import LayoutLMWrapper


class _FakeOutputs:
    def __init__(self, logits):
        self.logits = logits


class _FakeModel:
    def __call__(self, **kwargs):
        import torch

        batch, seq = kwargs["input_ids"].shape
        # 3-class logits
        return _FakeOutputs(torch.zeros(batch, seq, 3))


def test_layoutlm_wrapper_returns_logits_only() -> None:
    torch = pytest.importorskip("torch")
    wrapper = LayoutLMWrapper(_FakeModel())
    wrapper.eval()
    ids = torch.zeros(1, 4, dtype=torch.long)
    mask = torch.ones(1, 4, dtype=torch.long)
    bbox = torch.zeros(1, 4, 4, dtype=torch.long)
    tti = torch.zeros(1, 4, dtype=torch.long)
    out = wrapper(ids, mask, bbox, tti)
    assert tuple(out.shape) == (1, 4, 3)


def test_sample_inputs_are_fixed_sequence_length() -> None:
    torch = pytest.importorskip("torch")
    ids, mask, bbox, tti = _sample_inputs(1000, COREAI_SEQ_LENGTH)
    assert ids.shape == (1, COREAI_SEQ_LENGTH)
    assert mask.shape == (1, COREAI_SEQ_LENGTH)
    assert bbox.shape == (1, COREAI_SEQ_LENGTH, 4)
    assert tti.shape == (1, COREAI_SEQ_LENGTH)
    assert torch.all(bbox[..., 2] >= bbox[..., 0])
    assert torch.all(bbox[..., 3] >= bbox[..., 1])


def test_torch_export_succeeds_for_wrapper() -> None:
    torch = pytest.importorskip("torch")
    wrapper = LayoutLMWrapper(_FakeModel())
    wrapper.eval()
    sample = _sample_inputs(100, seq_len=16)
    with torch.no_grad():
        exported = torch.export.export(wrapper, args=sample)
    assert exported is not None


def test_write_export_sidecars_produces_swift_metadata(tmp_path: Path) -> None:
    checkpoint = tmp_path / "ckpt"
    checkpoint.mkdir()
    (checkpoint / "vocab.txt").write_text(
        "[PAD]\n[UNK]\nhello\n", encoding="utf-8"
    )
    (checkpoint / "tokenizer_config.json").write_text("{}\n", encoding="utf-8")

    class _Tok:
        def get_vocab(self):
            return {"[PAD]": 0, "[UNK]": 1, "hello": 2}

    out = tmp_path / "bundle"
    write_export_sidecars(
        checkpoint_path=checkpoint,
        output_path=out,
        tokenizer=_Tok(),
        config=SimpleNamespace(max_position_embeddings=512, vocab_size=3),
        id2label={0: "O", 1: "B-TOTAL"},
        label2id={"O": 0, "B-TOTAL": 1},
        num_labels=2,
        model_version="v1",
    )
    assert (out / "vocab.txt").exists()
    assert (out / "config.json").exists()
    assert (out / "label_map.json").exists()
    assert (out / "tokenizer_config.json").exists()
    config = (out / "config.json").read_text(encoding="utf-8")
    assert '"num_labels": 2' in config
