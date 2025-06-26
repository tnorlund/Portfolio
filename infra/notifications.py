"""SNS topic and notification infrastructure for step function failures."""

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output


class NotificationSystem(ComponentResource):
    """Centralized notification system for infrastructure failures."""

    def __init__(
        self,
        name: str,
        email_endpoints: list[str] | None = None,
        tags: dict[str, str] | None = None,
        opts: pulumi.ResourceOptions | None = None,
    ):
        """
        Create a notification system with SNS topic and subscriptions.

        Args:
            name: Base name for resources
            email_endpoints: List of email addresses to notify
            tags: Additional tags to apply to resources
            opts: Pulumi resource options
        """
        super().__init__("custom:infrastructure:NotificationSystem", name, None, opts)

        self.name = name
        self.tags = tags or {}
        self.tags.update({"Component": "NotificationSystem", "ManagedBy": "Pulumi"})

        # Create SNS topic for step function failures
        self.step_function_failure_topic = aws.sns.Topic(
            f"{name}-step-function-failures",
            display_name="Step Function Failure Notifications",
            tags=self.tags,
            opts=opts,
        )

        # Create SNS topic for critical errors
        self.critical_error_topic = aws.sns.Topic(
            f"{name}-critical-errors",
            display_name="Critical Infrastructure Errors",
            tags=self.tags,
            opts=opts,
        )

        # Create email subscriptions
        self.email_subscriptions = []
        if email_endpoints:
            for idx, email in enumerate(email_endpoints):
                # Step function failures
                sf_sub = aws.sns.TopicSubscription(
                    f"{name}-sf-failure-email-{idx}",
                    topic=self.step_function_failure_topic.arn,
                    protocol="email",
                    endpoint=email,
                    opts=pulumi.ResourceOptions(
                        parent=self,
                        depends_on=[self.step_function_failure_topic],
                    ),
                )
                self.email_subscriptions.append(sf_sub)

                # Critical errors
                critical_sub = aws.sns.TopicSubscription(
                    f"{name}-critical-email-{idx}",
                    topic=self.critical_error_topic.arn,
                    protocol="email",
                    endpoint=email,
                    opts=pulumi.ResourceOptions(
                        parent=self, depends_on=[self.critical_error_topic]
                    ),
                )
                self.email_subscriptions.append(critical_sub)

        # Create CloudWatch Event Rule for Step Function failures
        self.step_function_failure_rule = aws.cloudwatch.EventRule(
            f"{name}-step-function-failures",
            description="Capture all Step Function execution failures",
            event_pattern="""{
                "source": ["aws.states"],
                "detail-type": ["Step Functions Execution Status Change"],
                "detail": {
                    "status": ["FAILED", "TIMED_OUT", "ABORTED"]
                }
            }""",
            tags=self.tags,
            opts=opts,
        )

        # Create CloudWatch Event Target to send failures to SNS
        self.step_function_failure_target = aws.cloudwatch.EventTarget(
            f"{name}-sf-failure-target",
            rule=self.step_function_failure_rule.name,
            arn=self.step_function_failure_topic.arn,
            opts=pulumi.ResourceOptions(
                parent=self, depends_on=[self.step_function_failure_rule]
            ),
        )

        # Create IAM role for EventBridge to publish to SNS
        self.eventbridge_role = aws.iam.Role(
            f"{name}-eventbridge-sns-role",
            assume_role_policy="""{
                "Version": "2012-10-17",
                "Statement": [{
                    "Action": "sts:AssumeRole",
                    "Principal": {
                        "Service": "events.amazonaws.com"
                    },
                    "Effect": "Allow"
                }]
            }""",
            tags=self.tags,
            opts=opts,
        )

        # Create policy for EventBridge to publish to SNS
        self.eventbridge_sns_policy = aws.iam.Policy(
            f"{name}-eventbridge-sns-policy",
            policy=Output.all(
                sf_topic_arn=self.step_function_failure_topic.arn,
                critical_topic_arn=self.critical_error_topic.arn,
            ).apply(
                lambda args: f"""{{
                    "Version": "2012-10-17",
                    "Statement": [{{
                        "Effect": "Allow",
                        "Action": [
                            "sns:Publish"
                        ],
                        "Resource": [
                            "{args['sf_topic_arn']}",
                            "{args['critical_topic_arn']}"
                        ]
                    }}]
                }}"""
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Attach policy to role
        self.eventbridge_policy_attachment = aws.iam.RolePolicyAttachment(
            f"{name}-eventbridge-sns-attachment",
            role=self.eventbridge_role.name,
            policy_arn=self.eventbridge_sns_policy.arn,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[
                    self.eventbridge_role,
                    self.eventbridge_sns_policy,
                ],
            ),
        )

        # Update EventBridge rule to use the IAM role
        self.step_function_failure_rule_with_role = aws.cloudwatch.EventRule(
            f"{name}-step-function-failures-with-role",
            description="Capture all Step Function execution failures with proper permissions",
            event_pattern="""{
                "source": ["aws.states"],
                "detail-type": ["Step Functions Execution Status Change"],
                "detail": {
                    "status": ["FAILED", "TIMED_OUT", "ABORTED"]
                }
            }""",
            role_arn=self.eventbridge_role.arn,
            tags=self.tags,
            opts=pulumi.ResourceOptions(
                parent=self,
                depends_on=[
                    self.eventbridge_role,
                    self.eventbridge_policy_attachment,
                ],
                replace_on_changes=["*"],
            ),
        )

    def create_lambda_dlq_alarm(
        self,
        lambda_function: aws.lambda_.Function,
        dlq: aws.sqs.Queue,
        threshold: int = 1,
    ) -> aws.cloudwatch.MetricAlarm:
        """
        Create CloudWatch alarm for Lambda DLQ messages.

        Args:
            lambda_function: The Lambda function to monitor
            dlq: The dead letter queue to monitor
            threshold: Number of messages that trigger the alarm
        """
        alarm = aws.cloudwatch.MetricAlarm(
            f"{lambda_function._name}-dlq-alarm",
            name=f"{lambda_function._name}-dlq-messages",
            comparison_operator="GreaterThanThreshold",
            evaluation_periods=1,
            metric_name="ApproximateNumberOfMessagesVisible",
            namespace="AWS/SQS",
            period=300,
            statistic="Average",
            threshold=threshold,
            alarm_description=f"DLQ messages for {lambda_function._name}",
            alarm_actions=[self.critical_error_topic.arn],
            dimensions={"QueueName": dlq.name},
            tags=self.tags,
            opts=pulumi.ResourceOptions(parent=self),
        )
        return alarm

    def create_step_function_alarm(
        self,
        step_function_name: str,
        step_function_arn: Output[str],
        failure_threshold: int = 1,
    ) -> aws.cloudwatch.MetricAlarm:
        """
        Create CloudWatch alarm for step function failures.

        Args:
            step_function_name: Name of the step function
            step_function_arn: ARN of the step function
            failure_threshold: Number of failures that trigger the alarm
        """
        alarm = aws.cloudwatch.MetricAlarm(
            f"{step_function_name}-failure-alarm",
            name=f"{step_function_name}-execution-failures",
            comparison_operator="GreaterThanThreshold",
            evaluation_periods=1,
            metric_name="ExecutionsFailed",
            namespace="AWS/States",
            period=300,
            statistic="Sum",
            threshold=failure_threshold,
            alarm_description=f"Failed executions for {step_function_name}",
            alarm_actions=[self.step_function_failure_topic.arn],
            dimensions={"StateMachineArn": step_function_arn},
            tags=self.tags,
            opts=pulumi.ResourceOptions(parent=self),
        )
        return alarm

    @property
    def step_function_topic_arn(self) -> Output[str]:
        """Get the ARN of the step function failure topic."""
        return self.step_function_failure_topic.arn

    @property
    def critical_error_topic_arn(self) -> Output[str]:
        """Get the ARN of the critical error topic."""
        return self.critical_error_topic.arn
