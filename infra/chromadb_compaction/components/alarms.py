"""
CloudWatch alarms for the ChromaDB compaction pipeline.

The compaction handler already emits EMF metrics into the
``EmbeddingWorkflow`` namespace, but nothing alarmed on them: the 07-12
compaction storm ran for eleven days with CompactionLockAcquisitionFailed and
CompactionSnapshotUploadError firing continuously and no notification.

The handler emits these metrics without dimensions, so a single alarm per
metric covers every stack.  Thresholds are set to catch a sustained failure
mode rather than the occasional lock hand-off between the lines and words
invocations.
"""

from typing import Optional

import pulumi_aws as aws
from pulumi import ComponentResource, Input, ResourceOptions

# The handler's EmbeddedMetricsFormatter publishes to this namespace.
METRICS_NAMESPACE = "EmbeddingWorkflow"


class ChromaDBCompactionAlarms(ComponentResource):
    """CloudWatch alarms on the compaction handler's EMF metrics."""

    def __init__(
        self,
        name: str,
        alert_topic_arn: Optional[Input[str]] = None,
        stack: str = "dev",
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize compaction alarms.

        Args:
            name: The unique name of the resource
            alert_topic_arn: SNS topic notified when an alarm fires.  When
                omitted the alarms are still created (and visible in the
                console) but send no notification.
            stack: The Pulumi stack name, used for tagging
            opts: Optional resource options
        """
        super().__init__("chromadb:compaction:Alarms", name, None, opts)

        alarm_actions = [alert_topic_arn] if alert_topic_arn else None
        tags = {
            "Project": "ChromaDB",
            "Component": "CompactionAlarms",
            "Environment": stack,
            "ManagedBy": "Pulumi",
        }

        # Lock acquisition failures mean a compaction cycle returned its whole
        # batch for retry without doing any work.  A handful per hour is
        # normal contention between the lines and words invocations; a
        # sustained rate means the pipeline is spinning.
        self.lock_acquisition_alarm = aws.cloudwatch.MetricAlarm(
            f"{name}-lock-acquisition-failed",
            alarm_description=(
                "ChromaDB compaction is repeatedly failing to acquire the "
                "collection lock - batches are being retried without "
                "progress."
            ),
            metric_name="CompactionLockAcquisitionFailed",
            namespace=METRICS_NAMESPACE,
            statistic="Sum",
            period=300,  # 5 minutes
            evaluation_periods=3,  # sustained for 15 minutes
            threshold=20,
            comparison_operator="GreaterThanThreshold",
            alarm_actions=alarm_actions,
            ok_actions=alarm_actions,
            treat_missing_data="notBreaching",
            tags=tags,
            opts=ResourceOptions(parent=self),
        )

        # A failed snapshot upload discards a completed merge, so any
        # sustained rate means vectors are being silently dropped.
        self.snapshot_upload_alarm = aws.cloudwatch.MetricAlarm(
            f"{name}-snapshot-upload-error",
            alarm_description=(
                "ChromaDB compaction snapshot uploads are failing - merged "
                "vectors are being discarded."
            ),
            metric_name="CompactionSnapshotUploadError",
            namespace=METRICS_NAMESPACE,
            statistic="Sum",
            period=300,  # 5 minutes
            evaluation_periods=2,  # sustained for 10 minutes
            threshold=5,
            comparison_operator="GreaterThanThreshold",
            alarm_actions=alarm_actions,
            ok_actions=alarm_actions,
            treat_missing_data="notBreaching",
            tags=tags,
            opts=ResourceOptions(parent=self),
        )

        # Delta merges that fail leave a receipt without its vectors.  These
        # are rare enough that any occurrence is worth a notification.
        self.delta_merge_alarm = aws.cloudwatch.MetricAlarm(
            f"{name}-delta-merge-error",
            alarm_description=(
                "ChromaDB delta merges are failing - affected receipts have "
                "no vectors and their CompactionRuns stay pending."
            ),
            metric_name="CompactionDeltaMergeError",
            namespace=METRICS_NAMESPACE,
            statistic="Sum",
            period=300,  # 5 minutes
            evaluation_periods=1,
            threshold=0,
            comparison_operator="GreaterThanThreshold",
            alarm_actions=alarm_actions,
            ok_actions=alarm_actions,
            treat_missing_data="notBreaching",
            tags=tags,
            opts=ResourceOptions(parent=self),
        )

        self.register_outputs(
            {
                "lock_acquisition_alarm_arn": self.lock_acquisition_alarm.arn,
                "snapshot_upload_alarm_arn": self.snapshot_upload_alarm.arn,
                "delta_merge_alarm_arn": self.delta_merge_alarm.arn,
            }
        )


def create_chromadb_compaction_alarms(
    name: str = "chromadb-compaction",
    alert_topic_arn: Optional[Input[str]] = None,
    stack: str = "dev",
    opts: Optional[ResourceOptions] = None,
) -> ChromaDBCompactionAlarms:
    """
    Factory function to create ChromaDB compaction alarms.

    Args:
        name: Base name for the resources
        alert_topic_arn: SNS topic notified when an alarm fires
        stack: The Pulumi stack name, used for tagging
        opts: Optional resource options

    Returns:
        ChromaDBCompactionAlarms component resource
    """
    return ChromaDBCompactionAlarms(
        name,
        alert_topic_arn=alert_topic_arn,
        stack=stack,
        opts=opts,
    )
