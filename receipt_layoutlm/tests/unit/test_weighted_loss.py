"""Tests for class-weighted cross-entropy used by WeightedTrainer.

The trainer subclass itself is defined inline inside ReceiptLayoutLMTrainer.train(),
so this exercises the underlying loss formula directly to lock in:
  - weights=1 (uniform) reproduces vanilla CE
  - ignore_index=-100 is honored
  - higher weight on a class amplifies that class's loss component
"""

import pytest
import torch
import torch.nn as nn


def _weighted_loss(logits, labels, weights):
    """Same formula WeightedTrainer.compute_loss uses."""
    loss_fct = nn.CrossEntropyLoss(weight=weights, ignore_index=-100)
    return loss_fct(logits.view(-1, logits.size(-1)), labels.view(-1))


def test_uniform_weights_match_vanilla_ce():
    torch.manual_seed(0)
    n_classes = 5
    logits = torch.randn(2, 8, n_classes)
    labels = torch.randint(0, n_classes, (2, 8))
    weights_uniform = torch.ones(n_classes)
    vanilla = nn.CrossEntropyLoss(ignore_index=-100)(
        logits.view(-1, n_classes), labels.view(-1)
    )
    weighted = _weighted_loss(logits, labels, weights_uniform)
    assert torch.allclose(vanilla, weighted, atol=1e-6)


def test_ignore_index_minus_100_drops_those_positions():
    torch.manual_seed(0)
    n_classes = 3
    logits = torch.randn(1, 4, n_classes)
    # All-O labels, then mask everything → loss must be NaN (no contributing items)
    labels_all_ignored = torch.tensor([[-100, -100, -100, -100]])
    weights = torch.ones(n_classes)
    loss_ignored = _weighted_loss(logits, labels_all_ignored, weights)
    assert torch.isnan(loss_ignored), (
        "all-ignored batch should yield NaN (matches PyTorch CrossEntropy contract)"
    )
    # Same logits, one supervised position → finite loss
    labels_one = torch.tensor([[-100, 1, -100, -100]])
    loss_one = _weighted_loss(logits, labels_one, weights)
    assert torch.isfinite(loss_one)


def test_increasing_class_weight_amplifies_that_class_loss():
    # Construct logits that mispredict class 0 (true label) with high confidence.
    # Increasing weight on class 0 must increase the loss monotonically.
    torch.manual_seed(0)
    n_classes = 3
    # Logits predict class 1 confidently; true label is 0 → cross-entropy is high.
    logits = torch.tensor([[[-3.0, 5.0, -3.0]]])  # batch=1, seq=1
    labels = torch.tensor([[0]])
    losses = []
    for w0 in [0.5, 1.0, 2.0, 5.0]:
        weights = torch.tensor([w0, 1.0, 1.0])
        loss = _weighted_loss(logits, labels, weights)
        losses.append(loss.item())
    assert losses == sorted(losses), (
        f"loss should rise as class-0 weight rises, got {losses}"
    )


def test_class_weight_formula_inverse_frequency_clipped():
    """Reproduce the formula used in trainer.py to compute class weights."""
    # Skewed corpus: class 0 dominates; class 1 modestly rare; class 2 very rare; class 3 unseen.
    counts = [9000, 800, 50, 0, 200]
    total = sum(counts)  # 10050
    n_classes = len(counts)
    weights = []
    for c in counts:
        if c == 0:
            weights.append(1.0)
        else:
            w = total / (n_classes * c)
            weights.append(max(0.3, min(w, 5.0)))
    # Class 0 (majority): 10050/(5*9000)=0.223 → clipped to 0.3
    assert weights[0] == pytest.approx(0.3)
    # Class 1: 10050/(5*800)=2.51 → in range
    assert weights[1] == pytest.approx(2.51, rel=1e-2)
    # Class 2 (very rare): 10050/(5*50)=40.2 → clipped to 5.0
    assert weights[2] == pytest.approx(5.0)
    # Class 3 (unseen): falls back to 1.0
    assert weights[3] == pytest.approx(1.0)
    # Class 4: 10050/(5*200)=10.05 → clipped to 5.0
    assert weights[4] == pytest.approx(5.0)


def test_label_count_skips_minus_100():
    """The class-count loop in trainer.py must skip -100 ignore positions."""
    # Inline-copy of trainer.py's counting loop
    examples = [
        {"labels": [0, 1, 2, -100, 0]},
        {"labels": [-100, -100, 1, 1, 0]},
    ]
    n_classes = 3
    counts = [0] * n_classes
    for ex in examples:
        for lid in ex["labels"]:
            if lid == -100:
                continue
            if 0 <= lid < n_classes:
                counts[lid] += 1
    assert counts == [3, 3, 1]  # not [3, 3, 1, 3] (no -100 leakage)


def test_grad_accum_pattern_matches_sum_over_num_items():
    """When num_items_in_batch is provided (gradient accumulation in
    transformers 4.46+), the loss must be ``sum / num_items_in_batch`` so
    that accumulating K microbatches gives the same result as one big
    batch. Mirrors HF's standard ForTokenClassification loss pattern.
    """
    torch.manual_seed(0)
    n_classes = 4
    weights = torch.tensor([0.3, 1.0, 2.0, 5.0])
    # One "big batch" of 8 tokens with 6 supervised + 2 ignored
    logits_big = torch.randn(1, 8, n_classes)
    labels_big = torch.tensor([[0, 1, 2, 3, -100, 0, 1, -100]])
    num_items_big = int((labels_big != -100).sum())

    def weighted_loss(logits, labels, num_items):
        loss_fct = nn.CrossEntropyLoss(
            weight=weights, ignore_index=-100, reduction="sum"
        )
        loss = loss_fct(logits.view(-1, n_classes), labels.view(-1))
        return loss / num_items

    big_loss = weighted_loss(logits_big, labels_big, num_items_big)
    # Split into two microbatches of 4 tokens each (different supervised counts)
    accum_loss = weighted_loss(
        logits_big[:, :4], labels_big[:, :4], num_items_big
    ) + weighted_loss(
        logits_big[:, 4:], labels_big[:, 4:], num_items_big
    )
    assert torch.allclose(big_loss, accum_loss, atol=1e-6)
