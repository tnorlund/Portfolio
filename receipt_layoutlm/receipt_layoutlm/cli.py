import argparse
import os

from .config import DataConfig, TrainingConfig
from .trainer import ReceiptLayoutLMTrainer
from .inference import LayoutLMInference


def main() -> None:
    parser = argparse.ArgumentParser(description="Receipt LayoutLM CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    train_p = sub.add_parser("train", help="Train LayoutLM on DynamoDB data")
    train_p.add_argument("--job-name", required=True)
    train_p.add_argument(
        "--dynamo-table", default=os.getenv("DYNAMO_TABLE_NAME")
    )
    train_p.add_argument(
        "--region", default=os.getenv("AWS_REGION", "us-east-1")
    )
    train_p.add_argument("--epochs", type=int, default=10)
    train_p.add_argument("--batch-size", type=int, default=8)
    train_p.add_argument("--lr", type=float, default=5e-5)
    train_p.add_argument(
        "--pretrained", default="microsoft/layoutlm-base-uncased"
    )
    train_p.add_argument(
        "--warmup-ratio",
        type=float,
        default=None,
        help="Fraction of steps used for LR warmup. Defaults to config value.",
    )
    train_p.add_argument(
        "--gradient-accumulation-steps",
        type=int,
        default=None,
        help=(
            "Number of steps to accumulate gradients before optimizer step. "
            "Overrides config value."
        ),
    )
    train_p.add_argument(
        "--label-smoothing",
        type=float,
        default=None,
        help="Label smoothing factor (e.g., 0.05). Defaults to config value.",
    )
    train_p.add_argument(
        "--early-stopping-patience",
        type=int,
        default=None,
        help="Early stopping patience (epochs). Defaults to config value.",
    )
    train_p.add_argument(
        "--o-entity-ratio",
        type=float,
        default=None,
        help=(
            "Target O:entity token ratio for downsampling all-O lines in training. "
            "If set, overrides LAYOUTLM_O_TO_ENTITY_RATIO env."
        ),
    )
    train_p.add_argument(
        "--allowed-label",
        action="append",
        default=None,
        help=(
            "Whitelist of labels to keep (repeat flag). All others map to O. "
            "If omitted, uses all normalized CORE labels."
        ),
    )
    train_p.add_argument(
        "--merge-amounts",
        action="store_true",
        help=(
            "Map LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL to a single AMOUNT label "
            "before BIO tagging."
        ),
    )
    train_p.add_argument(
        "--merge-date-time",
        action="store_true",
        help=(
            "Map DATE and TIME to a single DATE label. "
            "Useful for matching SROIE dataset structure (4 labels)."
        ),
    )
    train_p.add_argument(
        "--merge-address-phone",
        action="store_true",
        help=(
            "Map ADDRESS_LINE and PHONE_NUMBER to a single ADDRESS label. "
            "Useful for matching SROIE dataset structure (4 labels)."
        ),
    )
    train_p.add_argument(
        "--dataset-snapshot-load",
        default=None,
        help=(
            "Path/URI to a saved tokenized dataset (DatasetDict.save_to_disk or HF repo). "
            "If set, skip DynamoDB load and preprocessing."
        ),
    )
    train_p.add_argument(
        "--dataset-snapshot-save",
        default=None,
        help=(
            "Directory/URI to save the tokenized dataset after preprocessing."
        ),
    )
    train_p.add_argument(
        "--output-s3-path",
        default=os.getenv("LAYOUTLM_TRAINING_BUCKET"),
        help=(
            "S3 bucket name or s3://bucket/prefix/ to sync trained model to. "
            "If bucket name only, will use s3://{bucket}/runs/{job_name}/. "
            "Defaults to LAYOUTLM_TRAINING_BUCKET env var."
        ),
    )

    infer_p = sub.add_parser("infer", help="Run LayoutLM inference")
    infer_p.add_argument(
        "--dynamo-table", default=os.getenv("DYNAMO_TABLE_NAME")
    )
    infer_p.add_argument(
        "--region", default=os.getenv("AWS_REGION", "us-east-1")
    )
    infer_p.add_argument("--image-id", required=True)
    infer_p.add_argument("--receipt-id", type=int, required=True)
    infer_p.add_argument(
        "--model-dir",
        default=os.path.expanduser("~/model"),
        help="Local directory containing model files",
    )
    infer_p.add_argument(
        "--model-s3-uri",
        default=None,
        help="Optional s3://bucket/prefix/ to sync model from",
    )
    infer_p.add_argument(
        "--auto-bucket-env",
        default="layoutlm_training_bucket",
        help=(
            "Env var containing S3 bucket name; will auto-resolve latest run if set"
        ),
    )

    args = parser.parse_args()

    if args.cmd == "train":
        if not args.dynamo_table:
            raise SystemExit(
                "--dynamo-table or DYNAMO_TABLE_NAME env is required"
            )

        data_cfg = DataConfig(
            dynamo_table_name=args.dynamo_table,
            aws_region=args.region,
        )
        train_cfg = TrainingConfig(
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.lr,
            pretrained_model_name=args.pretrained,
        )
        if args.warmup_ratio is not None:
            train_cfg.warmup_ratio = args.warmup_ratio
        if args.label_smoothing is not None:
            train_cfg.label_smoothing = args.label_smoothing
        if args.early_stopping_patience is not None:
            train_cfg.early_stopping_patience = args.early_stopping_patience
        if args.gradient_accumulation_steps is not None:
            train_cfg.gradient_accumulation_steps = (
                args.gradient_accumulation_steps
            )

        # Optional O:entity ratio for downsampling all-O lines (training only)
        if args.o_entity_ratio is not None:
            os.environ["LAYOUTLM_O_TO_ENTITY_RATIO"] = str(args.o_entity_ratio)

        # Optional label whitelist
        data_cfg.allowed_labels = (
            args.allowed_label if args.allowed_label else None
        )

        data_cfg.merge_amounts = bool(args.merge_amounts)
        # Set environment variables for label merging
        if args.merge_date_time:
            os.environ["LAYOUTLM_MERGE_DATE_TIME"] = "1"
        if args.merge_address_phone:
            os.environ["LAYOUTLM_MERGE_ADDRESS_PHONE"] = "1"
        data_cfg.dataset_snapshot_load = args.dataset_snapshot_load
        data_cfg.dataset_snapshot_save = args.dataset_snapshot_save
        train_cfg.output_s3_path = args.output_s3_path
        trainer = ReceiptLayoutLMTrainer(data_cfg, train_cfg)
        job_id = trainer.train(job_name=args.job_name)
        print(job_id)
    elif args.cmd == "infer":
        if not args.dynamo_table:
            raise SystemExit(
                "--dynamo-table or DYNAMO_TABLE_NAME env is required"
            )
        from receipt_dynamo import DynamoClient

        dyn = DynamoClient(table_name=args.dynamo_table, region=args.region)
        runner = LayoutLMInference(
            model_dir=args.model_dir,
            model_s3_uri=args.model_s3_uri,
            auto_from_bucket_env=args.auto_bucket_env,
        )
        res = runner.predict_receipt_from_dynamo(
            dyn, image_id=args.image_id, receipt_id=args.receipt_id
        )
        # Print concise JSON result to stdout
        import json as _json

        print(
            _json.dumps(
                {
                    "image_id": res.image_id,
                    "receipt_id": res.receipt_id,
                    "num_lines": len(res.lines),
                    "lines": [
                        {
                            "line_id": lp.line_id,
                            "tokens": lp.tokens,
                            "labels": lp.labels,
                        }
                        for lp in res.lines
                    ],
                }
            )
        )


if __name__ == "__main__":
    main()
