from receipt_layoutlm.trainer import (
    _checkpoint_metric_for_available_metrics,
    _checkpoint_metric_for_trainer,
    _epoch_metric_values,
    _metric_greater_is_better,
)


def test_checkpoint_metric_for_trainer_normalizes_aliases():
    assert _checkpoint_metric_for_trainer("f1") == "eval_f1"
    assert (
        _checkpoint_metric_for_trainer("product_detail_macro_f1")
        == "eval_product_detail_macro_f1"
    )
    assert _checkpoint_metric_for_trainer("eval_loss") == "eval_loss"
    assert _checkpoint_metric_for_trainer("accuracy") == "eval_accuracy"


def test_checkpoint_metric_fallback_preserves_seqeval_free_metrics():
    assert (
        _checkpoint_metric_for_available_metrics(
            "product_detail_macro_f1", seqeval_available=False
        )
        == "eval_accuracy"
    )
    assert (
        _checkpoint_metric_for_available_metrics(
            "loss", seqeval_available=False
        )
        == "eval_loss"
    )
    assert (
        _checkpoint_metric_for_available_metrics(
            "accuracy", seqeval_available=False
        )
        == "eval_accuracy"
    )


def test_metric_greater_is_better_treats_loss_as_lower_better():
    assert _metric_greater_is_better("eval_f1")
    assert not _metric_greater_is_better("eval_loss")


def test_epoch_metric_values_reads_prefixed_and_unprefixed_keys():
    rows = [
        {"epoch": 1, "eval_product_detail_macro_f1": 0.2},
        {"epoch": 2, "product_detail_macro_f1": 0.3},
        {"epoch": None, "eval_product_detail_macro_f1": 0.9},
    ]

    assert _epoch_metric_values(
        rows, "eval_product_detail_macro_f1"
    ) == [(1.0, 0.2), (2.0, 0.3)]
