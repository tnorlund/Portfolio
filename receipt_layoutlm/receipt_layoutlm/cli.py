import argparse
import json
import os
from typing import Dict, List, Optional

from .config import (
    MODEL_DEFAULTS,
    MERGE_PRESETS,
    DataConfig,
    ModelVersion,
    TrainingConfig,
)
from .export_coreml import export_coreml, export_from_s3
from .inference import LayoutLMInference
from .trainer import ReceiptLayoutLMTrainer
from .validate_coreml import validate_coreml


def _build_label_merges(
    args: argparse.Namespace,
) -> Optional[Dict[str, List[str]]]:
    """Build label_merges dict from CLI arguments.

    Priority order:
    1. --merge-preset (if specified and not 'none')
    2. --label-merges JSON (overrides/extends preset)
    3. Legacy boolean flags (only if no preset and no explicit merges)

    Args:
        args: Parsed CLI arguments.

    Returns:
        Dict mapping target labels to source labels, or None if no merges.
    """
    result: Dict[str, List[str]] = {}

    # 1. Apply preset if specified
    if getattr(args, "merge_preset", None):
        if args.merge_preset != "none":
            preset = MERGE_PRESETS.get(args.merge_preset)
            if preset:
                result.update(preset)

    # 2. Apply explicit JSON merges (overrides/extends preset)
    if getattr(args, "label_merges", None):
        try:
            explicit_merges = json.loads(args.label_merges)
            if not isinstance(explicit_merges, dict):
                raise SystemExit(
                    "--label-merges must be a JSON object, e.g., "
                    '\'{"AMOUNT": ["LINE_TOTAL", "SUBTOTAL"]}\''
                )
            result.update(explicit_merges)
        except json.JSONDecodeError as e:
            raise SystemExit(f"Invalid JSON for --label-merges: {e}") from e

    # 3. Apply legacy boolean flags (only if no preset and no explicit merges)
    if not getattr(args, "merge_preset", None) and not getattr(
        args, "label_merges", None
    ):
        if getattr(args, "merge_amounts", False):
            result["AMOUNT"] = ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"]
        if getattr(args, "merge_date_time", False):
            result["DATE"] = ["TIME"]
        if getattr(args, "merge_address_phone", False):
            result["ADDRESS"] = ["PHONE_NUMBER", "ADDRESS_LINE"]

    return result if result else None


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
        "--model-version",
        choices=["v1", "v3"],
        default="v1",
        help="LayoutLM model version: v1 (text+bbox) or v3 (text+bbox+image)",
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
        "--label-merges",
        type=str,
        default=None,
        help=(
            "JSON string of label merges. E.g., "
            '\'{"AMOUNT": ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"]}\'. '
            "Overrides legacy merge flags. Can be combined with --merge-preset."
        ),
    )
    train_p.add_argument(
        "--merge-preset",
        type=str,
        choices=["amounts", "date_time", "address_phone", "sroie", "none"],
        default=None,
        help=(
            "Use a predefined merge preset: "
            "'amounts' (amount labels → AMOUNT), "
            "'date_time' (TIME → DATE), "
            "'address_phone' (address labels → ADDRESS), "
            "'sroie' (all three for 4-label setup), "
            "'none' (no merging)."
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
    train_p.add_argument(
        "--export-coreml",
        action="store_true",
        help=(
            "Automatically queue CoreML export after training completes. "
            "Requires COREML_EXPORT_JOB_QUEUE_URL env var to be set."
        ),
    )
    train_p.add_argument(
        "--coreml-quantize",
        choices=["float16", "int8", "int4"],
        default="float16",
        help="Quantization mode for CoreML export (default: float16).",
    )
    train_p.add_argument(
        "--no-eval-heldout-windowed",
        action="store_true",
        help=(
            "Disable the in-training windowed held-out eval (which emits "
            "epochs.json live). On by default; pass this to skip the "
            "per-epoch eval cost."
        ),
    )
    train_p.add_argument(
        "--val-keys-s3",
        default=None,
        help=(
            "S3 URI of a JSON file pinning the canonical val receipt keys. "
            "When set, the run holds out exactly those receipts (shared across "
            "runs for direct comparability) instead of a random seeded split."
        ),
    )
    train_p.add_argument(
        "--scope",
        choices=["full", "line_items"],
        default="full",
        help=(
            "Training scope. 'full' (default) trains on the whole receipt. "
            "'line_items' crops each receipt to its line-item band (between the "
            "header and totals anchor labels) for a focused second-pass model."
        ),
    )
    train_p.add_argument(
        "--receipt-allowlist-s3",
        default=None,
        help=(
            'S3 URI of a JSON file ({"receipt_keys": ["img#rec", ...]}) '
            "restricting training/eval to a curated subset of receipts."
        ),
    )
    train_p.add_argument(
        "--synthetic-training-examples",
        default=None,
        help=(
            "Local path or S3 URI containing train-only LayoutLM-style synthetic "
            "examples generated from confusion heatmap recipes."
        ),
    )
    train_p.add_argument(
        "--resume-from-s3",
        default=None,
        help=(
            "S3 URI to a previous training run's output directory "
            "(e.g. s3://bucket/runs/layoutlm-hybrid-8-labels-v4/). "
            "Files are synced to /tmp/receipt_layoutlm/{job_name}/ "
            "before training, so the trainer's auto-resume picks up "
            "the latest checkpoint and continues training from there."
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

    # Export to CoreML subcommand
    export_p = sub.add_parser(
        "export-coreml", help="Export trained model to CoreML format"
    )
    export_p.add_argument(
        "--checkpoint-dir",
        help="Local directory containing model checkpoint",
    )
    export_p.add_argument(
        "--s3-uri",
        help="S3 URI to model checkpoint (s3://bucket/prefix/)",
    )
    export_p.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write CoreML bundle",
    )
    export_p.add_argument(
        "--model-name",
        default="LayoutLM",
        help="Name for the .mlpackage file (default: LayoutLM)",
    )
    export_p.add_argument(
        "--local-cache",
        help="Local directory to cache S3 downloads",
    )
    export_p.add_argument(
        "--quantize",
        choices=["float16", "int8", "int4"],
        default=None,
        help="Quantization mode for smaller model size",
    )
    export_p.add_argument(
        "--model-version",
        choices=["v1", "v3"],
        default="v1",
        help="LayoutLM model version: v1 (text+bbox) or v3 (text+bbox+image)",
    )

    # Validate CoreML subcommand
    validate_p = sub.add_parser(
        "validate-coreml", help="Validate CoreML model against PyTorch"
    )
    validate_p.add_argument(
        "--checkpoint-dir",
        required=True,
        help="Path to PyTorch checkpoint",
    )
    validate_p.add_argument(
        "--coreml-bundle",
        required=True,
        help="Path to CoreML bundle directory",
    )
    validate_p.add_argument(
        "--dynamo-table",
        default=os.getenv("DYNAMO_TABLE_NAME"),
        help="DynamoDB table for test data",
    )
    validate_p.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region",
    )
    validate_p.add_argument(
        "--num-samples",
        type=int,
        default=100,
        help="Number of samples to test",
    )
    validate_p.add_argument(
        "--output-json",
        help="Save detailed results to JSON file",
    )

    # Export worker subcommand (macOS only)
    worker_p = sub.add_parser(
        "export-worker",
        help="Run CoreML export worker (macOS only)",
    )
    worker_p.add_argument(
        "--job-queue-url",
        default=os.getenv("COREML_EXPORT_JOB_QUEUE_URL"),
        help="SQS URL for job queue (or COREML_EXPORT_JOB_QUEUE_URL env)",
    )
    worker_p.add_argument(
        "--results-queue-url",
        default=os.getenv("COREML_EXPORT_RESULTS_QUEUE_URL"),
        help="SQS URL for results queue (or COREML_EXPORT_RESULTS_QUEUE_URL env)",
    )
    worker_p.add_argument(
        "--dynamo-table",
        default=os.getenv("DYNAMO_TABLE_NAME"),
        help="DynamoDB table name for status updates",
    )
    worker_p.add_argument(
        "--region",
        default=os.getenv("AWS_REGION", "us-east-1"),
        help="AWS region",
    )
    worker_p.add_argument(
        "--once",
        action="store_true",
        help="Process one batch then exit (default: run continuously)",
    )
    worker_p.add_argument(
        "--continuous",
        action="store_true",
        help="Run continuously until interrupted (default behavior)",
    )

    # Evaluate-checkpoints subcommand: re-score every epoch on the frozen val set
    eval_p = sub.add_parser(
        "eval-checkpoints",
        help=(
            "Re-evaluate every saved checkpoint of a run on its frozen val "
            "set to prove which epoch generalizes best"
        ),
    )
    eval_p.add_argument(
        "--job-name",
        help="Training job name; run location is resolved via DynamoDB",
    )
    eval_p.add_argument(
        "--run-s3-uri",
        help=(
            "Explicit s3://bucket/runs/<job>/ prefix (overrides --job-name "
            "resolution)"
        ),
    )
    eval_p.add_argument(
        "--dynamo-table",
        default=os.getenv("DYNAMO_TABLE_NAME"),
        help="DynamoDB table (or DYNAMO_TABLE_NAME env)",
    )
    eval_p.add_argument(
        "--region",
        default=os.getenv("AWS_REGION", "us-east-1"),
        help="AWS region",
    )
    eval_p.add_argument(
        "--output-dir",
        default=os.getenv("SM_OUTPUT_DATA_DIR", "./epoch-eval"),
        help="Local directory for epochs.json + per-receipt JSON",
    )
    eval_p.add_argument(
        "--output-s3-uri",
        default=None,
        help="Optional s3://bucket/prefix/ to upload outputs to",
    )
    eval_p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override the run's recorded random seed (else read run.json)",
    )
    eval_p.add_argument(
        "--max-receipts",
        type=int,
        default=None,
        help="Cap val receipts evaluated (default: full frozen set)",
    )
    eval_p.add_argument(
        "--num-showcase",
        type=int,
        default=5,
        help="Val receipts to persist per epoch for scrubbing (default: 5)",
    )
    eval_p.add_argument(
        "--window-size",
        type=int,
        default=None,
        help=(
            "Inference window size; overrides the run's recorded value. "
            "Default: run.json window_size, else 200 (data_loader default)"
        ),
    )
    eval_p.add_argument(
        "--window-stride",
        type=int,
        default=None,
        help="Inference window stride; overrides the run's recorded value",
    )
    eval_p.add_argument(
        "--allow-hash-mismatch",
        action="store_true",
        help=(
            "Proceed even if the reconstructed val set no longer matches the "
            "run's recorded hash (data drift). Default fails loudly."
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
        pretrained = args.pretrained
        if (
            args.model_version == "v3"
            and pretrained == "microsoft/layoutlm-base-uncased"
        ):
            pretrained = MODEL_DEFAULTS[ModelVersion.V3]
        if args.model_version == "v1" and "layoutlmv3" in pretrained:
            raise SystemExit(
                f"--model-version v1 is incompatible with v3 model '{pretrained}'. Use --model-version v3."
            )
        if (
            args.model_version == "v3"
            and pretrained == "microsoft/layoutlm-base-uncased"
        ):
            raise SystemExit(
                f"--model-version v3 requires a v3 model, not '{pretrained}'."
            )
        train_cfg = TrainingConfig(
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.lr,
            pretrained_model_name=pretrained,
            model_version=args.model_version,
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

        # Pinned canonical val split (shared across runs for comparability).
        # Env drives the loader; data_cfg records it for Job-entity lineage.
        if getattr(args, "val_keys_s3", None):
            os.environ["LAYOUTLM_VAL_KEYS_S3"] = args.val_keys_s3
            data_cfg.val_keys_s3 = args.val_keys_s3

        # Scoped second-pass training: crop to the line-item band + curated subset.
        if getattr(args, "scope", "full") and args.scope != "full":
            os.environ["LAYOUTLM_SCOPE"] = args.scope
        if getattr(args, "receipt_allowlist_s3", None):
            os.environ["LAYOUTLM_RECEIPT_ALLOWLIST_S3"] = (
                args.receipt_allowlist_s3
            )
        if getattr(args, "synthetic_training_examples", None):
            os.environ["LAYOUTLM_SYNTHETIC_TRAINING_EXAMPLES"] = (
                args.synthetic_training_examples
            )
            data_cfg.synthetic_training_examples = (
                args.synthetic_training_examples
            )

        # Optional label whitelist
        data_cfg.allowed_labels = (
            args.allowed_label if args.allowed_label else None
        )

        # Build label merges from CLI arguments (preset, JSON, or legacy flags)
        data_cfg.label_merges = _build_label_merges(args)

        # Keep legacy merge_amounts for backwards compatibility with DataConfig
        data_cfg.merge_amounts = bool(args.merge_amounts)

        data_cfg.dataset_snapshot_load = args.dataset_snapshot_load
        data_cfg.dataset_snapshot_save = args.dataset_snapshot_save
        train_cfg.output_s3_path = args.output_s3_path
        train_cfg.auto_export_coreml = args.export_coreml
        train_cfg.coreml_quantize = args.coreml_quantize
        if getattr(args, "no_eval_heldout_windowed", False):
            train_cfg.eval_heldout_windowed = False
        trainer = ReceiptLayoutLMTrainer(data_cfg, train_cfg)
        if args.resume_from_s3:
            from .resume import sync_resume_checkpoint

            sync_resume_checkpoint(args.resume_from_s3, args.job_name)
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
    elif args.cmd == "export-coreml":
        if not args.checkpoint_dir and not args.s3_uri:
            raise SystemExit("Either --checkpoint-dir or --s3-uri is required")

        if args.s3_uri:
            bundle_path = export_from_s3(
                s3_uri=args.s3_uri,
                output_dir=args.output_dir,
                model_name=args.model_name,
                local_cache=args.local_cache,
                quantize=args.quantize,
                model_version=args.model_version,
            )
        else:
            bundle_path = export_coreml(
                checkpoint_dir=args.checkpoint_dir,
                output_dir=args.output_dir,
                model_name=args.model_name,
                quantize=args.quantize,
                model_version=args.model_version,
            )
        print(f"CoreML bundle created: {bundle_path}")

    elif args.cmd == "validate-coreml":
        result = validate_coreml(
            checkpoint_dir=args.checkpoint_dir,
            coreml_bundle_dir=args.coreml_bundle,
            dynamo_table=args.dynamo_table,
            region=args.region,
            num_samples=args.num_samples,
        )
        print(result)

        if args.output_json:
            import json as _json

            with open(args.output_json, "w") as f:
                _json.dump(result.to_dict(), f, indent=2)
            print(f"\nDetailed results saved to {args.output_json}")

    elif args.cmd == "export-worker":
        from .export_worker import check_macos, run_worker

        # Verify macOS
        check_macos()

        if not args.job_queue_url:
            raise SystemExit(
                "--job-queue-url or COREML_EXPORT_JOB_QUEUE_URL env is required"
            )
        if not args.results_queue_url:
            raise SystemExit(
                "--results-queue-url or COREML_EXPORT_RESULTS_QUEUE_URL env is required"
            )

        run_worker(
            job_queue_url=args.job_queue_url,
            results_queue_url=args.results_queue_url,
            dynamo_table=args.dynamo_table,
            region=args.region,
            run_once=args.once,
        )

    elif args.cmd == "eval-checkpoints":
        import logging
        from urllib.parse import urlparse

        from receipt_dynamo import DynamoClient

        from .evaluate_checkpoints import evaluate_run, sync_outputs_to_s3

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

        if not args.dynamo_table:
            raise SystemExit(
                "--dynamo-table or DYNAMO_TABLE_NAME env is required"
            )

        dyn = DynamoClient(table_name=args.dynamo_table, region=args.region)

        # Resolve the run's S3 location: explicit URI wins, else look it up by
        # job name (newest match) via the Job entity's storage prefixes.
        run_s3_uri = args.run_s3_uri
        job_name = args.job_name
        if not run_s3_uri:
            if not job_name:
                raise SystemExit(
                    "Provide --run-s3-uri or --job-name to locate the run"
                )
            jobs, _ = dyn.get_job_by_name(job_name, limit=1)
            if not jobs:
                raise SystemExit(f"No job found with name '{job_name}'")
            job = jobs[0]
            run_s3_uri = job.s3_uri_for_prefix("run_root_prefix")
            if not run_s3_uri:
                best = (job.results or {}).get("best_checkpoint_s3_path")
                if best:
                    # Strip a trailing best/ or checkpoint-*/ to get the run root
                    trimmed = best.rstrip("/").rsplit("/", 1)[0]
                    run_s3_uri = trimmed + "/"
            if not run_s3_uri:
                raise SystemExit(
                    f"Could not resolve run S3 location for job '{job_name}'. "
                    f"Pass --run-s3-uri explicitly."
                )
        if not job_name:
            job_name = run_s3_uri.rstrip("/").rsplit("/", 1)[-1]

        parsed = urlparse(run_s3_uri)
        bucket = parsed.netloc
        run_prefix = parsed.path.lstrip("/")

        payload = evaluate_run(
            dynamo=dyn,
            bucket=bucket,
            run_prefix=run_prefix,
            job_name=job_name,
            output_dir=args.output_dir,
            seed=args.seed,
            max_receipts=args.max_receipts,
            num_showcase=args.num_showcase,
            window_size=args.window_size,
            window_stride=args.window_stride,
            allow_hash_mismatch=args.allow_hash_mismatch,
        )

        if args.output_s3_uri:
            sync_outputs_to_s3(args.output_dir, args.output_s3_uri)

        best_epoch = payload.get("best_epoch_heldout")
        best_ckpt = payload.get("best_checkpoint_heldout")
        print(
            json.dumps(
                {
                    "job_name": payload["job_name"],
                    "num_checkpoints": len(payload["epochs"]),
                    "num_val_receipts": payload["num_val_receipts"],
                    "val_receipts_hash_verified": payload[
                        "val_receipts_hash_verified"
                    ],
                    "best_epoch_heldout": best_epoch,
                    "best_checkpoint_heldout": best_ckpt,
                    "best_epoch_training_reported": payload[
                        "best_epoch_training_reported"
                    ],
                    "output_dir": args.output_dir,
                    "output_s3_uri": args.output_s3_uri,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
