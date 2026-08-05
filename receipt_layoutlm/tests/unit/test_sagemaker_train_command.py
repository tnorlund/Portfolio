from infra.sagemaker_training.train import build_train_command


def test_build_train_command_maps_item_window_augmentation():
    cmd = build_train_command(
        {
            "job_name": "layoutlm-item-window",
            "dynamo_table": "ReceiptsTable-test",
            "item_window_augmentation": "true",
            "item_window_size": 96,
            "item_window_stride": 48,
        }
    )

    assert "--item-window-augment" in cmd
    assert cmd[cmd.index("--item-window-size") + 1] == "96"
    assert cmd[cmd.index("--item-window-stride") + 1] == "48"


def test_build_train_command_supports_item_window_augment_alias():
    cmd = build_train_command(
        {
            "job_name": "layoutlm-item-window",
            "dynamo_table": "ReceiptsTable-test",
            "item_window_augment": True,
        }
    )

    assert "--item-window-augment" in cmd


def test_build_train_command_maps_checkpoint_metric():
    cmd = build_train_command(
        {
            "job_name": "layoutlm-product-metric",
            "dynamo_table": "ReceiptsTable-test",
            "checkpoint_metric": "product_detail_macro_f1",
        }
    )

    assert cmd[cmd.index("--checkpoint-metric") + 1] == (
        "product_detail_macro_f1"
    )


def test_build_train_command_supports_metric_for_best_model_alias():
    cmd = build_train_command(
        {
            "job_name": "layoutlm-product-metric",
            "dynamo_table": "ReceiptsTable-test",
            "metric_for_best_model": "product_detail_macro_f1",
        }
    )

    assert cmd[cmd.index("--checkpoint-metric") + 1] == (
        "product_detail_macro_f1"
    )


def test_build_train_command_maps_product_detail_loss_weight():
    cmd = build_train_command(
        {
            "job_name": "layoutlm-product-weight",
            "dynamo_table": "ReceiptsTable-test",
            "product_detail_loss_weight": "1.5",
        }
    )

    assert cmd[cmd.index("--product-detail-loss-weight") + 1] == "1.5"


def test_build_train_command_forwards_val_keys_s3():
    cmd = build_train_command(
        {
            "job_name": "layoutlm-frozen",
            "dynamo_table": "ReceiptsTable-test",
            "val_keys_s3": "s3://bucket/splits/val_keys.json",
        }
    )

    assert cmd[cmd.index("--val-keys-s3") + 1] == (
        "s3://bucket/splits/val_keys.json"
    )
    assert "--no-frozen-val" not in cmd


def test_build_train_command_forwards_explicit_unfrozen_opt_out():
    """Without this hyperparameter the trainer refuses to start, by design."""
    cmd = build_train_command(
        {
            "job_name": "layoutlm-new-vocab",
            "dynamo_table": "ReceiptsTable-test",
            "no_frozen_val": "true",
        }
    )

    assert "--no-frozen-val" in cmd


def test_build_train_command_omits_opt_out_when_frozen_split_is_pinned():
    """The frozen split wins; the opt-out is an escape hatch, not an override."""
    cmd = build_train_command(
        {
            "job_name": "layoutlm-frozen",
            "dynamo_table": "ReceiptsTable-test",
            "val_keys_s3": "s3://bucket/splits/val_keys.json",
            "no_frozen_val": "true",
        }
    )

    assert "--no-frozen-val" not in cmd
    assert "--val-keys-s3" in cmd


def test_build_train_command_defaults_to_neither_val_split_flag():
    cmd = build_train_command(
        {
            "job_name": "layoutlm-default",
            "dynamo_table": "ReceiptsTable-test",
        }
    )

    assert "--no-frozen-val" not in cmd
    assert "--val-keys-s3" not in cmd
