"""Unit tests for shared parity metrics and NaN/Inf detection."""

from __future__ import annotations

import pytest

from receipt_layoutlm.exceptions import NonFiniteLogitsError
from receipt_layoutlm.validate_parity import (
    aggregate_word_predictions,
    assert_finite_logits,
    compare_predictions,
    count_nonfinite,
    summarize_comparisons,
)


def test_count_nonfinite_detects_nan_and_inf() -> None:
    import numpy as np

    arr = np.array([[1.0, float("nan")], [float("inf"), 2.0]])
    assert count_nonfinite(arr) == 2


def test_assert_finite_logits_raises() -> None:
    import numpy as np

    with pytest.raises(NonFiniteLogitsError) as exc:
        assert_finite_logits(np.array([1.0, float("nan")]), "CoreAI")
    assert exc.value.backend == "CoreAI"
    assert exc.value.bad_count == 1


def test_aggregate_word_predictions_mean_pools_subtokens() -> None:
    import numpy as np

    logits = np.array(
        [
            [0.0, 10.0],  # CLS
            [2.0, 0.0],  # word0 sub0
            [4.0, 0.0],  # word0 sub1
            [0.0, 5.0],  # word1
            [0.0, 0.0],  # SEP
        ],
        dtype=np.float64,
    )
    word_ids = [None, 0, 0, 1, None]
    id2label = {0: "O", 1: "B-TOTAL"}
    labels, confs, word_logits = aggregate_word_predictions(
        logits, word_ids, num_words=2, id2label=id2label
    )
    assert labels[0] == "O"
    assert labels[1] == "B-TOTAL"
    assert word_logits[0] == [3.0, 0.0]
    assert confs[0] > 0.5
    assert confs[1] > 0.5


def test_compare_predictions_reports_mismatch_and_rmse() -> None:
    cmp = compare_predictions(
        tokens=["A", "B"],
        pytorch_labels=["O", "B-TOTAL"],
        backend_labels=["O", "O"],
        pytorch_confs=[0.9, 0.8],
        backend_confs=[0.9, 0.2],
        pytorch_logits=[[1.0, 0.0], [0.0, 1.0]],
        backend_logits=[[1.0, 0.0], [1.0, 0.0]],
        backend_name="CoreAI",
        raise_on_nonfinite=True,
    )
    assert len(cmp["mismatches"]) == 1
    assert cmp["mismatches"][0]["token"] == "B"
    assert cmp["logit_rmses"]
    assert cmp["nan_inf_count"] == 0


def test_summarize_comparisons_computes_agreement() -> None:
    result = summarize_comparisons(
        all_pytorch_labels=["O", "B-TOTAL", "O"],
        all_backend_labels=["O", "O", "O"],
        all_pytorch_confs=[0.9, 0.8, 0.7],
        all_backend_confs=[0.9, 0.1, 0.7],
        all_logit_rmses=[0.0, 1.0],
        mismatches=[{"token": "x"}],
        backend_name="CoreAI",
        nan_inf_count=0,
    )
    assert result.total_tokens == 3
    assert result.matching_labels == 2
    assert abs(result.label_agreement_rate - 2 / 3) < 1e-9
    assert result.backend_name == "CoreAI"
    assert result.nan_inf_detected is False
    as_dict = result.to_dict()
    assert as_dict["num_mismatches"] == 1
