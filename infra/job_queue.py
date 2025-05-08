import pulumi
import pulumi_aws as aws
from pulumi import Output


class JobQueue:
    """
    Creates an SQS queue infrastructure for training job management.

    This class sets up:
    1. Main job queue for training jobs
    2. Dead letter queue for failed jobs
    3. Required IAM permissions
    4. CloudWatch alarms for monitoring
    """

    def __init__(self, name_prefix, env, tags=None):
        """
        Initialize the Job Queue infrastructure.

        Args:
            name_prefix (str): Prefix for resource names
            env (str): Environment name (dev, prod)
            tags (dict, optional): Tags to apply to all resources
        """
        self.name_prefix = name_prefix
        self.env = env
        self.tags = tags or {}

        # Add default tags
        self.tags.update(
            {"Environment": env, "Service": "JobQueue", "ManagedBy": "Pulumi"}
        )

        # Create resources
        self.dead_letter_queue = self._create_dlq()
        self.queue = self._create_main_queue()
        self.alarms = self._create_alarms()

    def _create_dlq(self):
        """Create a Dead Letter Queue for failed jobs."""
        dlq_name = f"{self.name_prefix}-job-dlq-{self.env}.fifo"

        dlq = aws.sqs.Queue(
            dlq_name,
            name=dlq_name,
            message_retention_seconds=1209600,  # 14 days
            fifo_queue=True,  # This queue must also be FIFO since main queue is FIFO
            content_based_deduplication=True,  # Enable content-based deduplication
            tags=self.tags,
        )

        return dlq

    def _create_main_queue(self):
        """Create the main job queue with priority support."""
        queue_name = f"{self.name_prefix}-job-queue-{self.env}.fifo"

        # Get the DLQ ARN for redrive policy
        dlq_arn = self.dead_letter_queue.arn

        # Create the main queue
        queue = aws.sqs.Queue(
            queue_name,
            name=queue_name,
            # Enable message content-based deduplication
            deduplication_scope="messageGroup",
            fifo_queue=True,  # Use FIFO for ordering guarantees
            fifo_throughput_limit="perMessageGroupId",
            content_based_deduplication=True,
            # Set redrive policy for failed messages
            redrive_policy=dlq_arn.apply(
                lambda arn: pulumi.Output.json_dumps(
                    {
                        "deadLetterTargetArn": arn,
                        "maxReceiveCount": 5,  # Try 5 times before sending to DLQ
                    }
                )
            ),
            # Set visibility timeout for processing jobs (30 minutes)
            visibility_timeout_seconds=1800,
            # Set message retention to 4 days
            message_retention_seconds=345600,
            tags=self.tags,
        )

        return queue

    def _create_alarms(self):
        """Create CloudWatch alarms for monitoring the queues."""
        alarms = []

        # Alarm for too many messages in the DLQ
        dlq_alarm = aws.cloudwatch.MetricAlarm(
            f"{self.name_prefix}-dlq-alarm-{self.env}",
            comparison_operator="GreaterThanThreshold",
            evaluation_periods=1,
            metric_name="ApproximateNumberOfMessagesVisible",
            namespace="AWS/SQS",
            period=300,
            statistic="Sum",
            threshold=10,
            alarm_description="Alert when too many messages in the Dead Letter Queue",
            dimensions={"QueueName": self.dead_letter_queue.name},
            alarm_actions=[],  # Add SNS topic ARNs here if needed
            tags=self.tags,
        )

        alarms.append(dlq_alarm)

        # Alarm for queue depth (may indicate stuck processing)
        queue_depth_alarm = aws.cloudwatch.MetricAlarm(
            f"{self.name_prefix}-queue-depth-alarm-{self.env}",
            comparison_operator="GreaterThanThreshold",
            evaluation_periods=3,
            metric_name="ApproximateNumberOfMessagesVisible",
            namespace="AWS/SQS",
            period=300,
            statistic="Average",
            threshold=100,  # Alert if more than 100 messages are queued for 15 minutes
            alarm_description="Alert when job queue has too many messages pending",
            dimensions={"QueueName": self.queue.name},
            alarm_actions=[],  # Add SNS topic ARNs here if needed
            tags=self.tags,
        )

        alarms.append(queue_depth_alarm)

        return alarms

    def get_queue_url(self):
        """Returns the job queue URL."""
        return self.queue.url

    def get_queue_arn(self):
        """Returns the job queue ARN."""
        return self.queue.arn

    def get_dlq_url(self):
        """Returns the dead letter queue URL."""
        return self.dead_letter_queue.url

    def get_dlq_arn(self):
        """Returns the dead letter queue ARN."""
        return self.dead_letter_queue.arn
