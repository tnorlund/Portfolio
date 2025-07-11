"""
Base class for Step Functions with comprehensive best practices.

This module provides a standardized base class that all Step Functions should
inherit from, ensuring consistent error handling, monitoring, and observability.
"""

import json
from typing import Any, Dict, List, Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions

from notifications import NotificationSystem
from step_function_utils import (
    add_error_handling_to_state,
    create_error_handler_state,
    create_fail_state,
    create_retry_policy,
)


class BaseStepFunction(ComponentResource):
    """
    Base class for AWS Step Functions with built-in best practices.

    Features:
    - Standardized error handling with SNS notifications
    - X-Ray tracing enabled by default
    - CloudWatch logging configuration
    - Retry policies with exponential backoff
    - Dead letter queue for failed executions
    - CloudWatch metrics and alarms
    - Consistent naming and tagging
    """

    def __init__(
        self,
        name: str,
        notification_system: NotificationSystem,
        opts: Optional[ResourceOptions] = None,
    ):
        """
        Initialize base Step Function.

        Args:
            name: Name for the Step Function and related resources
            notification_system: Notification system for error alerts
            opts: Pulumi resource options
        """
        super().__init__(f"step-functions-{name}", name, None, opts)

        self.name = name
        self.notification_system = notification_system
        self.stack = pulumi.get_stack()

        # Create IAM role for Step Function
        self.step_function_role = self._create_step_function_role()

        # Create CloudWatch log group
        self.log_group = self._create_log_group()

        # Create dead letter queue
        self.dlq = self._create_dead_letter_queue()

        # Initialize metrics
        self._setup_cloudwatch_alarms()

    def _create_step_function_role(self) -> aws.iam.Role:
        """Create IAM role with standard permissions for Step Functions."""
        role = aws.iam.Role(
            f"{self.name}-{self.stack}-sfn-role",
            name=f"{self.name}-{self.stack}-sfn-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "states.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            tags={
                "Component": self.name,
                "Stack": self.stack,
                "Type": "StepFunction",
            },
            opts=ResourceOptions(parent=self),
        )

        # Attach standard policies
        self._attach_standard_policies(role)

        return role

    def _attach_standard_policies(self, role: aws.iam.Role) -> None:
        """Attach standard policies for logging, X-Ray, and error handling."""
        # CloudWatch Logs policy
        logs_policy = aws.iam.RolePolicy(
            f"{self.name}-sfn-logs-policy",
            role=role.name,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                                "logs:DescribeLogStreams",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # X-Ray tracing policy
        xray_policy = aws.iam.RolePolicy(
            f"{self.name}-sfn-xray-policy",
            role=role.name,
            policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "xray:PutTraceSegments",
                                "xray:PutTelemetryRecords",
                                "xray:GetSamplingRules",
                                "xray:GetSamplingTargets",
                            ],
                            "Resource": "*",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # SNS publish policy for error notifications
        sns_policy = aws.iam.RolePolicy(
            f"{self.name}-sfn-sns-policy",
            role=role.name,
            policy=self.notification_system.step_function_topic_arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["sns:Publish"],
                                "Resource": arn,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # SQS policy for dead letter queue
        sqs_policy = aws.iam.RolePolicy(
            f"{self.name}-sfn-sqs-policy",
            role=role.name,
            policy=self.dlq.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:SendMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": arn,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

    def _create_log_group(self) -> aws.cloudwatch.LogGroup:
        """Create CloudWatch log group for Step Function logs."""
        return aws.cloudwatch.LogGroup(
            f"{self.name}-sfn-logs",
            name=f"/aws/stepfunctions/{self.name}-{self.stack}",
            retention_in_days=30,
            tags={
                "Component": self.name,
                "Stack": self.stack,
            },
            opts=ResourceOptions(parent=self),
        )

    def _create_dead_letter_queue(self) -> aws.sqs.Queue:
        """Create SQS dead letter queue for failed executions."""
        return aws.sqs.Queue(
            f"{self.name}-sfn-dlq",
            name=f"{self.name}-{self.stack}-sfn-dlq",
            message_retention_seconds=1209600,  # 14 days
            tags={
                "Component": self.name,
                "Stack": self.stack,
                "Type": "DeadLetterQueue",
            },
            opts=ResourceOptions(parent=self),
        )

    def _setup_cloudwatch_alarms(self) -> None:
        """Set up CloudWatch alarms for Step Function metrics."""
        # This will be implemented after the state machine is created
        # Alarms will monitor:
        # - Execution failures
        # - Execution timeouts
        # - Throttling
        # - Duration anomalies
        pass

    def create_lambda_invoke_policy(
        self, lambda_arns: List[Output[str]]
    ) -> aws.iam.Policy:
        """
        Create IAM policy for invoking Lambda functions.

        Args:
            lambda_arns: List of Lambda function ARNs

        Returns:
            IAM policy for Lambda invocation
        """
        return aws.iam.Policy(
            f"{self.name}-lambda-invoke-policy",
            description=f"Lambda invoke policy for {self.name} Step Function",
            policy=Output.all(*lambda_arns).apply(
                lambda arns: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": arns,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

    def enhance_state_definition(
        self, state_definition: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Enhance a state definition with error handling and monitoring.

        Args:
            state_definition: Raw state machine definition

        Returns:
            Enhanced state definition with error handling
        """
        enhanced = state_definition.copy()

        # Add global timeout if not present
        if "TimeoutSeconds" not in enhanced:
            enhanced["TimeoutSeconds"] = 3600  # 1 hour default

        # Process each state
        states = enhanced.get("States", {})
        error_states = {}

        for state_name, state_def in states.items():
            if state_def.get("Type") in ["Task", "Map", "Parallel"]:
                # Add retry policy if not present
                if "Retry" not in state_def:
                    state_def["Retry"] = [
                        create_retry_policy(
                            error_types=[
                                "States.TaskFailed",
                                "States.Timeout",
                            ],
                            max_attempts=3,
                        ),
                        create_retry_policy(
                            error_types=[
                                "Lambda.ServiceException",
                                "Lambda.AWSLambdaException",
                            ],
                            interval_seconds=1,
                            max_attempts=5,
                            backoff_rate=1.5,
                        ),
                    ]

                # Add error catching
                if "Catch" not in state_def:
                    error_handler_name = f"{state_name}ErrorHandler"
                    state_def["Catch"] = [
                        {
                            "ErrorEquals": ["States.ALL"],
                            "Next": error_handler_name,
                            "ResultPath": "$.error",
                        }
                    ]

                    # Create error handler state
                    error_states[error_handler_name] = {
                        "Type": "Task",
                        "Resource": "arn:aws:states:::sns:publish",
                        "Parameters": {
                            "TopicArn": self.notification_system.step_function_topic_arn,
                            "Subject": f"Error in {self.name} - {state_name}",
                            "Message.$": "States.Format('Error in state {}: {}', $.error.Error, $.error.Cause)",
                        },
                        "ResultPath": "$.notificationResult",
                        "Next": f"{state_name}Failed",
                    }

                    # Create fail state
                    error_states[f"{state_name}Failed"] = {
                        "Type": "Fail",
                        "Error": f"{state_name}ExecutionFailed",
                        "Cause": "State execution failed. Check CloudWatch logs.",
                    }

        # Add error states to definition
        enhanced["States"].update(error_states)

        return enhanced

    def create_state_machine(
        self,
        definition: Dict[str, Any],
        enable_xray: bool = True,
        enable_logging: bool = True,
    ) -> aws.sfn.StateMachine:
        """
        Create the Step Function state machine with best practices.

        Args:
            definition: State machine definition
            enable_xray: Enable X-Ray tracing
            enable_logging: Enable CloudWatch logging

        Returns:
            Step Function state machine
        """
        # Enhance definition with error handling
        enhanced_definition = self.enhance_state_definition(definition)

        # Create logging configuration
        logging_config = None
        if enable_logging:
            logging_config = aws.sfn.StateMachineLoggingConfigurationArgs(
                level="ALL",
                include_execution_data=True,
                destinations=[
                    aws.sfn.StateMachineLoggingConfigurationDestinationArgs(
                        cloud_watch_logs_log_group=aws.sfn.StateMachineLoggingConfigurationDestinationCloudWatchLogsLogGroupArgs(
                            log_group_arn=self.log_group.arn.apply(
                                lambda arn: f"{arn}:*"
                            ),
                        ),
                    ),
                ],
            )

        # Create tracing configuration
        tracing_config = None
        if enable_xray:
            tracing_config = aws.sfn.StateMachineTracingConfigurationArgs(
                enabled=True,
            )

        # Create state machine
        state_machine = aws.sfn.StateMachine(
            f"{self.name}-state-machine",
            name=f"{self.name}-{self.stack}",
            role_arn=self.step_function_role.arn,
            definition=Output.all(
                sns_topic_arn=self.notification_system.step_function_topic_arn
            ).apply(lambda args: json.dumps(enhanced_definition)),
            logging_configuration=logging_config,
            tracing_configuration=tracing_config,
            tags={
                "Component": self.name,
                "Stack": self.stack,
                "Type": "StepFunction",
            },
            opts=ResourceOptions(parent=self),
        )

        # Create CloudWatch alarms
        self._create_execution_alarms(state_machine)

        return state_machine

    def _create_execution_alarms(
        self, state_machine: aws.sfn.StateMachine
    ) -> None:
        """Create CloudWatch alarms for state machine execution metrics."""
        # Failed executions alarm
        failed_alarm = aws.cloudwatch.MetricAlarm(
            f"{self.name}-failed-executions-alarm",
            name=f"{self.name}-{self.stack}-failed-executions",
            comparison_operator="GreaterThanThreshold",
            evaluation_periods=1,
            metric_name="ExecutionsFailed",
            namespace="AWS/States",
            period=300,  # 5 minutes
            statistic="Sum",
            threshold=5,  # Alert if >5 failures in 5 minutes
            alarm_description=f"Alert when {self.name} Step Function has failed executions",
            alarm_actions=[self.notification_system.alarm_topic_arn],
            dimensions={
                "StateMachineArn": state_machine.arn,
            },
            tags={
                "Component": self.name,
                "Stack": self.stack,
            },
            opts=ResourceOptions(parent=self),
        )

        # Timeout alarm
        timeout_alarm = aws.cloudwatch.MetricAlarm(
            f"{self.name}-timeout-alarm",
            name=f"{self.name}-{self.stack}-timeouts",
            comparison_operator="GreaterThanThreshold",
            evaluation_periods=1,
            metric_name="ExecutionsTimedOut",
            namespace="AWS/States",
            period=300,
            statistic="Sum",
            threshold=3,
            alarm_description=f"Alert when {self.name} Step Function has timeouts",
            alarm_actions=[self.notification_system.alarm_topic_arn],
            dimensions={
                "StateMachineArn": state_machine.arn,
            },
            tags={
                "Component": self.name,
                "Stack": self.stack,
            },
            opts=ResourceOptions(parent=self),
        )

        # Duration anomaly alarm
        duration_alarm = aws.cloudwatch.MetricAlarm(
            f"{self.name}-duration-alarm",
            name=f"{self.name}-{self.stack}-duration",
            comparison_operator="GreaterThanThreshold",
            evaluation_periods=2,
            metric_name="ExecutionTime",
            namespace="AWS/States",
            period=300,
            statistic="Average",
            threshold=300000,  # 5 minutes in milliseconds
            alarm_description=f"Alert when {self.name} Step Function runs too long",
            alarm_actions=[self.notification_system.alarm_topic_arn],
            dimensions={
                "StateMachineArn": state_machine.arn,
            },
            tags={
                "Component": self.name,
                "Stack": self.stack,
            },
            opts=ResourceOptions(parent=self),
        )
