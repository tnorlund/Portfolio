import argparse
import os

from .config import DataConfig, TrainingConfig
from .trainer import ReceiptLayoutLMTrainer


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

        trainer = ReceiptLayoutLMTrainer(data_cfg, train_cfg)
        job_id = trainer.train(job_name=args.job_name)
        print(job_id)


if __name__ == "__main__":
    main()
