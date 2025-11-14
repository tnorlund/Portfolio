"""CloudWatch billing alerts for cost monitoring."""

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions


class BillingAlerts(ComponentResource):
    """Component for creating CloudWatch billing alerts."""

    def __init__(
        self,
        name: str,
        sns_topic_arn: Output[str],
        thresholds: dict[str, float] | None = None,
        tags: dict[str, str] | None = None,
        opts: ResourceOptions | None = None,
    ):
        """
        Create CloudWatch billing alerts for cost monitoring.

        Args:
            name: Base name for resources
            sns_topic_arn: ARN of SNS topic to send alerts to
            thresholds: Dictionary of threshold names to dollar amounts
                       Default: {"warning": 10.0, "critical": 25.0, "emergency": 50.0}
            tags: Additional tags to apply to resources
            opts: Pulumi resource options
        """
        super().__init__(
            "custom:infrastructure:BillingAlerts", name, None, opts
        )

        self.name = name
        self.sns_topic_arn = sns_topic_arn
        self.tags = tags or {}
        self.tags.update({"Component": "BillingAlerts", "ManagedBy": "Pulumi"})

        # Default thresholds for CloudWatch Custom Metrics costs
        default_thresholds = {
            "warning": 10.0,  # $10/month
            "critical": 25.0,  # $25/month
            "emergency": 50.0,  # $50/month
        }
        self.thresholds = thresholds or default_thresholds

        # Get account ID for metric dimensions
        current = aws.get_caller_identity()
        account_id = current.account_id

        # Create alarms for CloudWatch Custom Metrics costs
        self.alarms = {}
        for threshold_name, threshold_value in self.thresholds.items():
            alarm = aws.cloudwatch.MetricAlarm(
                f"{name}-cloudwatch-metrics-{threshold_name}",
                name=f"{name}-cloudwatch-custom-metrics-{threshold_name}",
                comparison_operator="GreaterThanThreshold",
                evaluation_periods=1,
                metric_name="EstimatedCharges",
                namespace="AWS/Billing",
                period=86400,  # 24 hours (daily check)
                statistic="Maximum",
                threshold=threshold_value,
                alarm_description=(
                    f"Alert when CloudWatch Custom Metrics costs exceed "
                    f"${threshold_value}/month ({threshold_name} threshold)"
                ),
                alarm_actions=[sns_topic_arn],
                dimensions={
                    "Currency": "USD",
                    "ServiceName": "AmazonCloudWatch",
                },
                treat_missing_data="notBreaching",  # Don't alarm if metric is missing
                tags=self.tags,
                opts=ResourceOptions(parent=self),
            )
            self.alarms[threshold_name] = alarm

        # Also create a general CloudWatch service cost alarm (broader)
        self.general_alarm = aws.cloudwatch.MetricAlarm(
            f"{name}-cloudwatch-service-general",
            name=f"{name}-cloudwatch-service-costs",
            comparison_operator="GreaterThanThreshold",
            evaluation_periods=1,
            metric_name="EstimatedCharges",
            namespace="AWS/Billing",
            period=86400,  # 24 hours
            statistic="Maximum",
            threshold=100.0,  # $100/month for all CloudWatch services
            alarm_description=(
                "Alert when total CloudWatch service costs exceed $100/month"
            ),
            alarm_actions=[sns_topic_arn],
            dimensions={
                "Currency": "USD",
                "ServiceName": "AmazonCloudWatch",
            },
            treat_missing_data="notBreaching",
            tags=self.tags,
            opts=ResourceOptions(parent=self),
        )

    @property
    def alarm_arns(self) -> dict[str, Output[str]]:
        """Get ARNs of all created alarms."""
        return {name: alarm.arn for name, alarm in self.alarms.items()}





