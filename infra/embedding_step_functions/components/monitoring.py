"""Monitoring and alerting component for embedding infrastructure."""

import json
from typing import Optional, Dict, Any

from pulumi import (
    ComponentResource,
    Output,
    ResourceOptions,
)
from pulumi_aws.cloudwatch import (
    MetricAlarm,
    Dashboard,
)
from pulumi_aws.sns import Topic, TopicSubscription

from .base import stack


class MonitoringComponent(ComponentResource):
    """Component for creating CloudWatch monitoring and alerting resources."""

    def __init__(
        self,
        name: str,
        lambda_functions: Dict[str, Any],
        step_functions: Dict[str, Any],
        notification_email: Optional[str] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        """Initialize monitoring component.

        Args:
            name: Component name
            lambda_functions: Dictionary of Lambda functions to monitor
            step_functions: Dictionary of Step Functions to monitor
            notification_email: Email for alert notifications
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:embedding:Monitoring",
            name,
            None,
            opts,
        )

        self.lambda_functions = lambda_functions
        self.step_functions = step_functions
        self.notification_email = notification_email

        # Create SNS topic for alerts
        self._create_sns_topic()

        # Create Lambda monitoring alarms
        self._create_lambda_alarms()

        # Create Step Function monitoring alarms
        self._create_step_function_alarms()

        # Create CloudWatch dashboard
        self._create_dashboard()

        # Register outputs
        self.register_outputs(
            {
                "alert_topic_arn": self.alert_topic.arn,
                "dashboard_url": Output.concat(
                    "https://console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=",
                    self.dashboard.dashboard_name,
                ),
            }
        )

    def _create_sns_topic(self):
        """Create SNS topic for alert notifications."""
        self.alert_topic = Topic(
            f"embedding-alerts-{stack}",
            display_name="Embedding Workflow Alerts",
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Subscribe email if provided
        if self.notification_email:
            TopicSubscription(
                f"embedding-alerts-email-{stack}",
                topic=self.alert_topic.arn,
                protocol="email",
                endpoint=self.notification_email,
                opts=ResourceOptions(parent=self),
            )

    def _create_lambda_alarms(self):
        """Create CloudWatch alarms for Lambda functions."""
        self.lambda_alarms = {}

        for name, lambda_func in self.lambda_functions.items():
            function_name = lambda_func.name

            # Lambda timeout alarm (80% of timeout duration)
            timeout_alarm = MetricAlarm(
                f"{name}-timeout-alarm-{stack}",
                alarm_description=f"Lambda {name} approaching timeout",
                metric_name="Duration",
                namespace="AWS/Lambda",
                statistic="Maximum",
                period=300,  # 5 minutes
                evaluation_periods=1,
                threshold=lambda_func.timeout.apply(lambda t: t * 1000 * 0.8),  # 80% of timeout in ms
                comparison_operator="GreaterThanThreshold",
                dimensions={
                    "FunctionName": function_name,
                },
                alarm_actions=[self.alert_topic.arn],
                treat_missing_data="notBreaching",
                tags={"environment": stack},
                opts=ResourceOptions(parent=self),
            )

            # Lambda error rate alarm
            error_alarm = MetricAlarm(
                f"{name}-error-alarm-{stack}",
                alarm_description=f"Lambda {name} high error rate",
                metric_name="ErrorRate",
                namespace="AWS/Lambda",
                statistic="Average",
                period=300,  # 5 minutes
                evaluation_periods=2,
                threshold=5.0,  # 5% error rate
                comparison_operator="GreaterThanThreshold",
                dimensions={
                    "FunctionName": function_name,
                },
                alarm_actions=[self.alert_topic.arn],
                treat_missing_data="notBreaching",
                tags={"environment": stack},
                opts=ResourceOptions(parent=self),
            )

            # Lambda throttle alarm
            throttle_alarm = MetricAlarm(
                f"{name}-throttle-alarm-{stack}",
                alarm_description=f"Lambda {name} being throttled",
                metric_name="Throttles",
                namespace="AWS/Lambda",
                statistic="Sum",
                period=300,  # 5 minutes
                evaluation_periods=1,
                threshold=1,  # Any throttling
                comparison_operator="GreaterThanOrEqualToThreshold",
                dimensions={
                    "FunctionName": function_name,
                },
                alarm_actions=[self.alert_topic.arn],
                treat_missing_data="notBreaching",
                tags={"environment": stack},
                opts=ResourceOptions(parent=self),
            )

            self.lambda_alarms[name] = {
                "timeout": timeout_alarm,
                "error": error_alarm,
                "throttle": throttle_alarm,
            }

    def _create_step_function_alarms(self):
        """Create CloudWatch alarms for Step Functions."""
        self.step_function_alarms = {}

        for name, step_func in self.step_functions.items():
            state_machine_name = step_func.name

            # Step Function execution failure alarm
            execution_failure_alarm = MetricAlarm(
                f"{name}-execution-failure-alarm-{stack}",
                alarm_description=f"Step Function {name} execution failures",
                metric_name="ExecutionsFailed",
                namespace="AWS/States",
                statistic="Sum",
                period=300,  # 5 minutes
                evaluation_periods=1,
                threshold=1,  # Any failure
                comparison_operator="GreaterThanOrEqualToThreshold",
                dimensions={
                    "StateMachineArn": step_func.arn,
                },
                alarm_actions=[self.alert_topic.arn],
                treat_missing_data="notBreaching",
                tags={"environment": stack},
                opts=ResourceOptions(parent=self),
            )

            # Step Function execution timeout alarm
            execution_timeout_alarm = MetricAlarm(
                f"{name}-execution-timeout-alarm-{stack}",
                alarm_description=f"Step Function {name} execution timeouts",
                metric_name="ExecutionTime",
                namespace="AWS/States",
                statistic="Maximum",
                period=300,  # 5 minutes
                evaluation_periods=1,
                threshold=1800000,  # 30 minutes in milliseconds
                comparison_operator="GreaterThanThreshold",
                dimensions={
                    "StateMachineArn": step_func.arn,
                },
                alarm_actions=[self.alert_topic.arn],
                treat_missing_data="notBreaching",
                tags={"environment": stack},
                opts=ResourceOptions(parent=self),
            )

            self.step_function_alarms[name] = {
                "execution_failure": execution_failure_alarm,
                "execution_timeout": execution_timeout_alarm,
            }

    def _create_dashboard(self):
        """Create CloudWatch dashboard for monitoring."""
        # Build widgets for the dashboard
        widgets = []

        # Lambda function metrics
        lambda_widgets = self._create_lambda_widgets()
        widgets.extend(lambda_widgets)

        # Step Function metrics
        step_function_widgets = self._create_step_function_widgets()
        widgets.extend(step_function_widgets)

        # Custom metrics widgets
        custom_widgets = self._create_custom_metrics_widgets()
        widgets.extend(custom_widgets)

        dashboard_body = {
            "widgets": widgets,
            "start": "-PT1H",  # Last 1 hour
            "end": "P0D",
            "period": 300,
        }

        self.dashboard = Dashboard(
            f"embedding-dashboard-{stack}",
            dashboard_name=f"EmbeddingWorkflows-{stack}",
            dashboard_body=json.dumps(dashboard_body),
            opts=ResourceOptions(parent=self),
        )

    def _create_lambda_widgets(self) -> list:
        """Create CloudWatch widgets for Lambda monitoring."""
        widgets = []
        
        # Lambda duration widget
        lambda_metrics = []
        for name, lambda_func in self.lambda_functions.items():
            lambda_metrics.append([
                "AWS/Lambda", "Duration", "FunctionName", lambda_func.name.apply(str),
                {"label": name}
            ])

        duration_widget = {
            "type": "metric",
            "x": 0, "y": 0, "width": 12, "height": 6,
            "properties": {
                "metrics": lambda_metrics,
                "view": "timeSeries",
                "stacked": False,
                "region": "us-east-1",
                "title": "Lambda Function Duration",
                "period": 300,
                "stat": "Average",
                "yAxis": {
                    "left": {"min": 0}
                }
            }
        }
        widgets.append(duration_widget)

        # Lambda error rate widget
        error_metrics = []
        for name, lambda_func in self.lambda_functions.items():
            error_metrics.append([
                "AWS/Lambda", "Errors", "FunctionName", lambda_func.name.apply(str),
                {"label": f"{name} Errors"}
            ])
            error_metrics.append([
                "AWS/Lambda", "Invocations", "FunctionName", lambda_func.name.apply(str),
                {"label": f"{name} Invocations"}
            ])

        error_widget = {
            "type": "metric",
            "x": 12, "y": 0, "width": 12, "height": 6,
            "properties": {
                "metrics": error_metrics,
                "view": "timeSeries",
                "stacked": False,
                "region": "us-east-1",
                "title": "Lambda Function Errors & Invocations",
                "period": 300,
                "stat": "Sum"
            }
        }
        widgets.append(error_widget)

        return widgets

    def _create_step_function_widgets(self) -> list:
        """Create CloudWatch widgets for Step Function monitoring."""
        widgets = []

        # Step Function execution status widget
        sf_metrics = []
        for name, step_func in self.step_functions.items():
            sf_metrics.append([
                "AWS/States", "ExecutionsSucceeded", "StateMachineArn", step_func.arn.apply(str),
                {"label": f"{name} Succeeded"}
            ])
            sf_metrics.append([
                "AWS/States", "ExecutionsFailed", "StateMachineArn", step_func.arn.apply(str),
                {"label": f"{name} Failed"}
            ])

        sf_widget = {
            "type": "metric",
            "x": 0, "y": 6, "width": 12, "height": 6,
            "properties": {
                "metrics": sf_metrics,
                "view": "timeSeries",
                "stacked": False,
                "region": "us-east-1",
                "title": "Step Function Executions",
                "period": 300,
                "stat": "Sum"
            }
        }
        widgets.append(sf_widget)

        # Step Function execution time widget
        time_metrics = []
        for name, step_func in self.step_functions.items():
            time_metrics.append([
                "AWS/States", "ExecutionTime", "StateMachineArn", step_func.arn.apply(str),
                {"label": f"{name} Duration"}
            ])

        time_widget = {
            "type": "metric",
            "x": 12, "y": 6, "width": 12, "height": 6,
            "properties": {
                "metrics": time_metrics,
                "view": "timeSeries",
                "stacked": False,
                "region": "us-east-1",
                "title": "Step Function Execution Time",
                "period": 300,
                "stat": "Average",
                "yAxis": {
                    "left": {"min": 0}
                }
            }
        }
        widgets.append(time_widget)

        return widgets

    def _create_custom_metrics_widgets(self) -> list:
        """Create widgets for custom application metrics."""
        widgets = []

        # Custom metrics for operation timing
        custom_widget = {
            "type": "metric",
            "x": 0, "y": 12, "width": 24, "height": 6,
            "properties": {
                "metrics": [
                    ["EmbeddingWorkflow", "OpenAIAPICallDuration", "Operation", "poll"],
                    ["EmbeddingWorkflow", "S3OperationDuration", "Operation", "upload"],
                    ["EmbeddingWorkflow", "S3OperationDuration", "Operation", "download"],
                    ["EmbeddingWorkflow", "ChromaDBOperationDuration", "Operation", "save_delta"],
                ],
                "view": "timeSeries",
                "stacked": False,
                "region": "us-east-1",
                "title": "Custom Operation Metrics",
                "period": 300,
                "stat": "Average"
            }
        }
        widgets.append(custom_widget)

        return widgets