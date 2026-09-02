"""Receipt update pipeline: summary/line-item queues, updaters, and the
DynamoDB stream processor — relocated OUT of infra/chromadb_compaction
(teardown PR #2 of the Chroma removal).

These resources are Chroma-independent: the stream processor fans label/
place/section/summary events to the summary and line-item updaters (and
runs the native vector-freshening leg), none of which touch Chroma. They
historically lived inside the chromadb_compaction component only because
the stream processor ALSO fed the lines/words compaction queues — which
it still does here via the passed-in URLs, until the compaction stack is
deleted and those two env vars are dropped.

EVERY resource in this module is an alias-preserving MOVE: logical names
are byte-identical to their old names and each resource carries an
explicit ``pulumi.Alias`` to its old URN (old parent chain
``chromadb:compaction:Infrastructure$chromadb:queues:SQSQueues`` or
``$chromadb:compaction:HybridLambda``), so Pulumi updates state in place
and the physical queues/Lambdas (whose URLs/names are baked into
deployed env vars) are never replaced. A correct deploy of this move
shows ZERO create/replace for the moved resources — verify with
``pulumi preview`` before merging any change here.

The one intentional exception: a NEW dedicated IAM role (the old shared
role stays behind with the compaction Lambda). Repointing a Lambda's
``role`` is an in-place update, not a replacement.
"""

# pylint: disable=too-many-instance-attributes,too-many-statements
# pylint: disable=too-many-locals

import json
from pathlib import Path
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import Alias, ComponentResource, Output, ResourceOptions

from lambda_layer import dynamo_layer, dynamo_stream_layer

_REPO_ROOT = Path(__file__).parent.parent.parent
_COMPACTION_DIR = Path(__file__).parent.parent / "chromadb_compaction"


def _old_urn(child_type: str, logical_name: str, *, chain: str) -> str:
    """URN of a resource under its OLD chromadb_compaction parent chain."""
    stack = pulumi.get_stack()
    project = pulumi.get_project()
    return (
        f"urn:pulumi:{stack}::{project}::"
        f"chromadb:compaction:Infrastructure${chain}${child_type}"
        f"::{logical_name}"
    )


def _moved(child_type: str, logical_name: str, *, chain: str, parent):
    """ResourceOptions for an alias-preserving move from the old chain."""
    return ResourceOptions(
        parent=parent,
        aliases=[Alias(urn=_old_urn(child_type, logical_name, chain=chain))],
    )


_QUEUES_CHAIN = "chromadb:queues:SQSQueues"
_LAMBDA_CHAIN = "chromadb:compaction:HybridLambda"


class ReceiptUpdateQueues(ComponentResource):
    """Summary/line-item update pipeline + DynamoDB stream processor."""

    def __init__(
        self,
        name: str,
        *,
        queues_name: str,
        lambdas_name: str,
        dynamodb_table_arn: pulumi.Input[str],
        dynamodb_stream_arn: pulumi.Input[str],
        lines_queue_url: pulumi.Input[str],
        words_queue_url: pulumi.Input[str],
        lines_queue_arn: pulumi.Input[str],
        words_queue_arn: pulumi.Input[str],
        stack: Optional[str] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        """``queues_name``/``lambdas_name`` MUST equal the old component's
        child prefixes (``chromadb-{stack}-queues`` / ``chromadb-{stack}``)
        so auto-named physical resources keep their identities.

        ``lines/words_queue_*`` keep the stream processor publishing to
        the compaction queues until the compaction stack is deleted; drop
        them (and the two env vars) in that later PR.
        """
        super().__init__("receipt:update:Queues", name, None, opts)
        if stack is None:
            stack = pulumi.get_stack()

        # ------------------------------------------------------------------
        # Queues (moved from ChromaDBQueues; logical names byte-identical)
        # ------------------------------------------------------------------
        self.summary_dlq = aws.sqs.Queue(
            f"{queues_name}-summary-dlq",
            message_retention_seconds=1209600,  # 14 days
            visibility_timeout_seconds=300,  # 5 minutes
            receive_wait_time_seconds=0,  # Short polling
            tags={
                "Project": "ChromaDB",
                "Component": "Summary-DLQ",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=_moved(
                "aws:sqs/queue:Queue",
                f"{queues_name}-summary-dlq",
                chain=_QUEUES_CHAIN,
                parent=self,
            ),
        )

        self.summary_queue = aws.sqs.Queue(
            f"{queues_name}-summary-queue",
            message_retention_seconds=345600,  # 4 days
            visibility_timeout_seconds=120,  # 2x summary updater timeout
            receive_wait_time_seconds=20,  # Long polling
            delay_seconds=15,  # batch multiple changes per receipt
            redrive_policy=Output.all(self.summary_dlq.arn).apply(
                lambda args: json.dumps(
                    {"deadLetterTargetArn": args[0], "maxReceiveCount": 3}
                )
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "Summary-Queue",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=_moved(
                "aws:sqs/queue:Queue",
                f"{queues_name}-summary-queue",
                chain=_QUEUES_CHAIN,
                parent=self,
            ),
        )

        self.line_item_dlq = aws.sqs.Queue(
            f"{queues_name}-line-item-dlq",
            message_retention_seconds=1209600,
            visibility_timeout_seconds=300,
            receive_wait_time_seconds=0,
            tags={
                "Project": "ChromaDB",
                "Component": "LineItem-DLQ",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=_moved(
                "aws:sqs/queue:Queue",
                f"{queues_name}-line-item-dlq",
                chain=_QUEUES_CHAIN,
                parent=self,
            ),
        )

        self.line_item_queue = aws.sqs.Queue(
            f"{queues_name}-line-item-queue",
            message_retention_seconds=345600,
            visibility_timeout_seconds=240,  # 2x line-item Lambda timeout
            receive_wait_time_seconds=20,
            delay_seconds=15,
            redrive_policy=Output.all(self.line_item_dlq.arn).apply(
                lambda args: json.dumps(
                    {"deadLetterTargetArn": args[0], "maxReceiveCount": 3}
                )
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "LineItem-Queue",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=_moved(
                "aws:sqs/queue:Queue",
                f"{queues_name}-line-item-queue",
                chain=_QUEUES_CHAIN,
                parent=self,
            ),
        )

        # Queue policy (moved): consumer-only statements — the old
        # component passed no producer_role_arns, so the policy body is
        # exactly this Lambda-consume statement.
        self.summary_queue_policy = aws.sqs.QueuePolicy(
            f"{queues_name}-summary-queue-policy",
            queue_url=self.summary_queue.url,
            policy=Output.all(self.summary_queue.arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Sid": "AllowCompactorLambdaConsume",
                                "Effect": "Allow",
                                "Principal": {
                                    "Service": "lambda.amazonaws.com"
                                },
                                "Action": [
                                    "sqs:ReceiveMessage",
                                    "sqs:DeleteMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": args[0],
                            }
                        ],
                    }
                )
            ),
            opts=_moved(
                "aws:sqs/queuePolicy:QueuePolicy",
                f"{queues_name}-summary-queue-policy",
                chain=_QUEUES_CHAIN,
                parent=self,
            ),
        )

        # ------------------------------------------------------------------
        # Dedicated IAM role (NEW — the old shared role stays with the
        # compaction Lambda). Repointing the moved Lambdas is in-place.
        # ------------------------------------------------------------------
        self.lambda_role = aws.iam.Role(
            f"{name}-lambda-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                        }
                    ],
                }
            ),
            tags={
                "Project": "ReceiptUpdates",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=ResourceOptions(parent=self),
        )

        aws.iam.RolePolicy(
            f"{name}-dynamodb-policy",
            role=self.lambda_role.id,
            policy=Output.all(dynamodb_table_arn).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeStream",
                                    "dynamodb:GetRecords",
                                    "dynamodb:GetShardIterator",
                                    "dynamodb:ListStreams",
                                ],
                                "Resource": [
                                    args[0],
                                    f"{args[0]}/stream/*",
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:DeleteItem",
                                    "dynamodb:BatchWriteItem",
                                    "dynamodb:Query",
                                    "dynamodb:DescribeTable",
                                ],
                                "Resource": [
                                    args[0],
                                    f"{args[0]}/index/*",
                                ],
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self.lambda_role),
        )

        aws.iam.RolePolicy(
            f"{name}-sqs-policy",
            role=self.lambda_role.id,
            policy=Output.all(
                self.summary_queue.arn,
                self.summary_dlq.arn,
                self.line_item_queue.arn,
                self.line_item_dlq.arn,
                lines_queue_arn,
                words_queue_arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:SendMessage",
                                    "sqs:SendMessageBatch",
                                    "sqs:ReceiveMessage",
                                    "sqs:DeleteMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": list(args),
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self.lambda_role),
        )

        aws.iam.RolePolicy(
            f"{name}-cloudwatch-policy",
            role=self.lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "cloudwatch:PutMetricData",
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self.lambda_role),
        )

        # Line-item updater's cross-service grants (moved policy bodies;
        # new resources on the new role — old copies die with the old
        # role's attachment when the old component drops them).
        aws.iam.RolePolicy(
            f"{name}-line-item-invoke-reocr-policy",
            role=self.lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": "lambda:InvokeFunction",
                            "Resource": (
                                "arn:aws:lambda:*:*:function:"
                                f"trigger-reocr-{stack}-trigger-reocr"
                            ),
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self.lambda_role),
        )
        aws.iam.RolePolicy(
            f"{name}-line-item-refine-queue-policy",
            role=self.lambda_role.id,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "sqs:SendMessage",
                                "sqs:GetQueueUrl",
                            ],
                            "Resource": (
                                "arn:aws:sqs:*:*:"
                                f"upload-images-{stack}-ocr-queue"
                            ),
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self.lambda_role),
        )

        # ------------------------------------------------------------------
        # Log groups (moved)
        # ------------------------------------------------------------------
        self.stream_log_group = aws.cloudwatch.LogGroup(
            f"{lambdas_name}-stream-log-group",
            retention_in_days=14,
            tags={
                "Project": "ChromaDB",
                "Component": "StreamProcessor",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=_moved(
                "aws:cloudwatch/logGroup:LogGroup",
                f"{lambdas_name}-stream-log-group",
                chain=_LAMBDA_CHAIN,
                parent=self,
            ),
        )
        self.summary_updater_log_group = aws.cloudwatch.LogGroup(
            f"{lambdas_name}-summary-updater-log-group",
            retention_in_days=14,
            tags={
                "Project": "ChromaDB",
                "Component": "SummaryUpdater",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=_moved(
                "aws:cloudwatch/logGroup:LogGroup",
                f"{lambdas_name}-summary-updater-log-group",
                chain=_LAMBDA_CHAIN,
                parent=self,
            ),
        )
        self.line_item_updater_log_group = aws.cloudwatch.LogGroup(
            f"{lambdas_name}-line-item-updater-log-group",
            retention_in_days=14,
            tags={
                "Project": "ChromaDB",
                "Component": "LineItemUpdater",
                "Environment": stack,
                "ManagedBy": "Pulumi",
            },
            opts=_moved(
                "aws:cloudwatch/logGroup:LogGroup",
                f"{lambdas_name}-line-item-updater-log-group",
                chain=_LAMBDA_CHAIN,
                parent=self,
            ),
        )

        # ------------------------------------------------------------------
        # Stream processor (moved whole; env unchanged incl. lines/words
        # URLs until the compaction stack is deleted)
        # ------------------------------------------------------------------
        _stream_lambdas = _COMPACTION_DIR / "lambdas"
        self.stream_processor_function = aws.lambda_.Function(
            f"{lambdas_name}-stream-processor",
            runtime="python3.13",
            architectures=["arm64"],
            code=pulumi.AssetArchive(
                {
                    "stream_processor.py": pulumi.FileAsset(
                        str(_stream_lambdas / "stream_processor.py")
                    ),
                    "utils/__init__.py": pulumi.FileAsset(
                        str(_stream_lambdas / "utils" / "__init__.py")
                    ),
                    "utils/aws_clients.py": pulumi.FileAsset(
                        str(_stream_lambdas / "utils" / "aws_clients.py")
                    ),
                    "utils/logging.py": pulumi.FileAsset(
                        str(_stream_lambdas / "utils" / "logging.py")
                    ),
                    "utils/metrics.py": pulumi.FileAsset(
                        str(_stream_lambdas / "utils" / "metrics.py")
                    ),
                    "utils/response.py": pulumi.FileAsset(
                        str(_stream_lambdas / "utils" / "response.py")
                    ),
                    "utils/timeout_handler.py": pulumi.FileAsset(
                        str(_stream_lambdas / "utils" / "timeout_handler.py")
                    ),
                    "utils/tracing.py": pulumi.FileAsset(
                        str(_stream_lambdas / "utils" / "tracing.py")
                    ),
                    "utils/sqs_batching.py": pulumi.FileAsset(
                        str(_stream_lambdas / "utils" / "sqs_batching.py")
                    ),
                    "utils/lambda_types.py": pulumi.FileAsset(
                        str(_stream_lambdas / "utils" / "lambda_types.py")
                    ),
                }
            ),
            handler="stream_processor.lambda_handler",
            role=self.lambda_role.arn,
            timeout=120,
            memory_size=256,
            environment={
                "variables": {
                    "LINES_QUEUE_URL": lines_queue_url,
                    "WORDS_QUEUE_URL": words_queue_url,
                    "RECEIPT_SUMMARY_QUEUE_URL": self.summary_queue.url,
                    "LINE_ITEM_QUEUE_URL": self.line_item_queue.url,
                    "DYNAMO_TABLE_NAME": Output.from_input(
                        dynamodb_table_arn
                    ).apply(lambda arn: arn.split("/")[-1]),
                    "LOG_LEVEL": "INFO",
                    "MAX_RECORDS_PER_INVOCATION": "10",
                    "LAMBDA_TIMEOUT_SECONDS": "120",
                    "MAX_CONSECUTIVE_FAILURES": "10",
                }
            },
            description=(
                "Processes DynamoDB stream events for ChromaDB metadata "
                "synchronization"
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "StreamProcessor",
                "Environment": stack,
                "ManagedBy": "Pulumi",
                "environment": stack,
            },
            layers=[dynamo_layer.arn, dynamo_stream_layer.arn],
            opts=ResourceOptions(
                parent=self,
                aliases=[
                    Alias(
                        urn=_old_urn(
                            "aws:lambda/function:Function",
                            f"{lambdas_name}-stream-processor",
                            chain=_LAMBDA_CHAIN,
                        )
                    )
                ],
                depends_on=[self.lambda_role, self.stream_log_group],
                ignore_changes=["layers"],
            ),
        )

        # ------------------------------------------------------------------
        # Summary updater (moved)
        # ------------------------------------------------------------------
        _summary_dir = Path(__file__).parent.parent / "receipt_summary_updater"
        summary_updater_code = pulumi.AssetArchive(
            {
                "handler.py": pulumi.FileAsset(
                    str(_summary_dir / "handler.py")
                ),
                "summary_processor.py": pulumi.FileAsset(
                    str(_summary_dir / "summary_processor.py")
                ),
                "receipt_upload/__init__.py": pulumi.StringAsset(
                    "# deploy-time stub: only tender is bundled; the real\n"
                    "# receipt_upload __init__ pulls PIL and the full "
                    "package.\n"
                ),
                "receipt_upload/tender.py": pulumi.FileAsset(
                    str(
                        _REPO_ROOT
                        / "receipt_upload"
                        / "receipt_upload"
                        / "tender.py"
                    )
                ),
            }
        )
        self.summary_updater_function = aws.lambda_.Function(
            f"{lambdas_name}-summary-updater",
            runtime="python3.13",
            architectures=["arm64"],
            code=summary_updater_code,
            handler="handler.lambda_handler",
            role=self.lambda_role.arn,
            timeout=60,
            memory_size=256,
            environment={
                "variables": {
                    "DYNAMODB_TABLE_NAME": Output.all(
                        dynamodb_table_arn
                    ).apply(lambda args: args[0].split("/")[-1]),
                    "LOG_LEVEL": "INFO",
                }
            },
            description=(
                "Recomputes ReceiptSummary when ReceiptWordLabel or "
                "ReceiptPlace records change"
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "SummaryUpdater",
                "Environment": stack,
                "ManagedBy": "Pulumi",
                "environment": stack,
            },
            layers=[dynamo_layer.arn],
            opts=ResourceOptions(
                parent=self,
                aliases=[
                    Alias(
                        urn=_old_urn(
                            "aws:lambda/function:Function",
                            f"{lambdas_name}-summary-updater",
                            chain=_LAMBDA_CHAIN,
                        )
                    )
                ],
                depends_on=[
                    self.lambda_role,
                    self.summary_updater_log_group,
                ],
                ignore_changes=["layers"],
            ),
        )

        # ------------------------------------------------------------------
        # Line-item updater (moved; canonical FileAssets unchanged)
        # ------------------------------------------------------------------
        _li_dir = Path(__file__).parent.parent / "receipt_line_item_updater"
        _ru = _REPO_ROOT / "receipt_upload" / "receipt_upload"
        line_item_updater_code = pulumi.AssetArchive(
            {
                "handler.py": pulumi.FileAsset(str(_li_dir / "handler.py")),
                "line_item_processor.py": pulumi.FileAsset(
                    str(_li_dir / "line_item_processor.py")
                ),
                "receipt_upload/__init__.py": pulumi.StringAsset(
                    "# deploy-time stub: only line_items is bundled; the "
                    "real\n# receipt_upload __init__ pulls PIL and the full "
                    "package.\n"
                ),
                "receipt_upload/line_items/__init__.py": pulumi.StringAsset(
                    "# namespace stub\n"
                ),
                "receipt_upload/line_items/geometry.py": pulumi.FileAsset(
                    str(_ru / "line_items" / "geometry.py")
                ),
                "receipt_upload/line_items/blocks.py": pulumi.FileAsset(
                    str(_ru / "line_items" / "blocks.py")
                ),
                "receipt_upload/line_items/provenance.py": pulumi.FileAsset(
                    str(_ru / "line_items" / "provenance.py")
                ),
                "receipt_upload/line_items/reocr.py": pulumi.FileAsset(
                    str(_ru / "line_items" / "reocr.py")
                ),
                "receipt_upload/line_items/reocr_strategy.py": (
                    pulumi.FileAsset(
                        str(_ru / "line_items" / "reocr_strategy.py")
                    )
                ),
                "receipt_upload/geometry/__init__.py": pulumi.StringAsset(
                    "# namespace stub\n"
                ),
                "receipt_upload/geometry/transformations.py": (
                    pulumi.FileAsset(
                        str(_ru / "geometry" / "transformations.py")
                    )
                ),
                "receipt_upload/line_items/assets/block_role_priors_v1.json": (
                    pulumi.FileAsset(
                        str(
                            _ru
                            / "line_items"
                            / "assets"
                            / "block_role_priors_v1.json"
                        )
                    )
                ),
                "receipt_upload/line_items/assets/block_role_priors_v2.json": (
                    pulumi.FileAsset(
                        str(
                            _ru
                            / "line_items"
                            / "assets"
                            / "block_role_priors_v2.json"
                        )
                    )
                ),
                "receipt_upload/line_items/assets/reocr_ladder.json": (
                    pulumi.FileAsset(
                        str(
                            _ru / "line_items" / "assets" / "reocr_ladder.json"
                        )
                    )
                ),
            }
        )
        self.line_item_updater_function = aws.lambda_.Function(
            f"{lambdas_name}-line-item-updater",
            runtime="python3.13",
            architectures=["arm64"],
            code=line_item_updater_code,
            handler="handler.lambda_handler",
            role=self.lambda_role.arn,
            timeout=120,
            memory_size=512,
            environment={
                "variables": {
                    "DYNAMODB_TABLE_NAME": Output.all(
                        dynamodb_table_arn
                    ).apply(lambda args: args[0].split("/")[-1]),
                    "LOG_LEVEL": "INFO",
                    "TRIGGER_REOCR_FUNCTION_NAME": (
                        f"trigger-reocr-{stack}-trigger-reocr"
                    ),
                    "OCR_JOB_QUEUE_NAME": (f"upload-images-{stack}-ocr-queue"),
                    "ENABLE_LINE_ITEM_REFINE": (
                        pulumi.Config("chromadb").get(
                            "enable-line-item-refine"
                        )
                        or "false"
                    ),
                }
            },
            description=(
                "Rewrites RECEIPT_LINE_ITEM rows via the band-block decoder "
                "when a receipt's summary changes"
            ),
            tags={
                "Project": "ChromaDB",
                "Component": "LineItemUpdater",
                "Environment": stack,
                "ManagedBy": "Pulumi",
                "environment": stack,
            },
            layers=[dynamo_layer.arn],
            opts=ResourceOptions(
                parent=self,
                aliases=[
                    Alias(
                        urn=_old_urn(
                            "aws:lambda/function:Function",
                            f"{lambdas_name}-line-item-updater",
                            chain=_LAMBDA_CHAIN,
                        )
                    )
                ],
                depends_on=[
                    self.lambda_role,
                    self.line_item_updater_log_group,
                ],
                ignore_changes=["layers"],
            ),
        )

        # ------------------------------------------------------------------
        # Event source mappings (moved)
        # ------------------------------------------------------------------
        self.stream_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{lambdas_name}-stream-event-source-mapping",
            event_source_arn=dynamodb_stream_arn,
            function_name=self.stream_processor_function.arn,
            starting_position="LATEST",
            batch_size=10,
            maximum_batching_window_in_seconds=5,
            parallelization_factor=5,
            maximum_retry_attempts=3,
            maximum_record_age_in_seconds=3600,
            bisect_batch_on_function_error=True,
            opts=_moved(
                "aws:lambda/eventSourceMapping:EventSourceMapping",
                f"{lambdas_name}-stream-event-source-mapping",
                chain=_LAMBDA_CHAIN,
                parent=self,
            ),
        )
        self.summary_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{lambdas_name}-summary-event-source-mapping",
            event_source_arn=self.summary_queue.arn,
            function_name=self.summary_updater_function.arn,
            batch_size=100,
            maximum_batching_window_in_seconds=5,
            function_response_types=["ReportBatchItemFailures"],
            opts=_moved(
                "aws:lambda/eventSourceMapping:EventSourceMapping",
                f"{lambdas_name}-summary-event-source-mapping",
                chain=_LAMBDA_CHAIN,
                parent=self,
            ),
        )
        self.line_item_event_source_mapping = aws.lambda_.EventSourceMapping(
            f"{lambdas_name}-line-item-event-source-mapping",
            event_source_arn=self.line_item_queue.arn,
            function_name=self.line_item_updater_function.arn,
            batch_size=50,
            maximum_batching_window_in_seconds=5,
            function_response_types=["ReportBatchItemFailures"],
            opts=_moved(
                "aws:lambda/eventSourceMapping:EventSourceMapping",
                f"{lambdas_name}-line-item-event-source-mapping",
                chain=_LAMBDA_CHAIN,
                parent=self,
            ),
        )

        # Exports
        self.summary_queue_url = self.summary_queue.url
        self.summary_queue_arn = self.summary_queue.arn
        self.line_item_queue_url = self.line_item_queue.url
        self.line_item_queue_arn = self.line_item_queue.arn
        self.stream_processor_arn = self.stream_processor_function.arn

        self.register_outputs(
            {
                "summary_queue_url": self.summary_queue_url,
                "line_item_queue_url": self.line_item_queue_url,
                "stream_processor_arn": self.stream_processor_arn,
            }
        )
