"""Pulumi component for handling EC2 Spot Instance interruption notifications."""

import pulumi
import pulumi_aws as aws
from pulumi import ResourceOptions


class SpotInterruptionHandler:
    """Pulumi component for handling EC2 Spot Instance interruption notifications."""

    def __init__(self, name, instance_role_name, sns_email=None, opts=None):
        """Initialize SpotInterruptionHandler.

        Args:
            name: Base name for the created resources
            instance_role_name: Name of the IAM role used by EC2 instances
            sns_email: Optional email address to receive interruption notifications
            opts: Optional resource options
        """
        # Create SNS topic for spot interruption notifications
        self.sns_topic = aws.sns.Topic(
            f"{name}-spot-interruption",
            display_name="EC2 Spot Instance Interruption Notifications",
            opts=opts,
        )

        # Add email subscription if provided
        if sns_email:
            self.email_subscription = aws.sns.TopicSubscription(
                f"{name}-spot-interruption-email",
                topic=self.sns_topic.arn,
                protocol="email",
                endpoint=sns_email,
                opts=opts,
            )

        # Create CloudWatch Event Rule for spot interruption
        self.event_rule = aws.cloudwatch.EventRule(
            f"{name}-spot-interruption-rule",
            description="Captures EC2 Spot Instance Interruption Warnings",
            event_pattern="""
            {
                "source": ["aws.ec2"],
                "detail-type": ["EC2 Spot Instance Interruption Warning"]
            }
            """,
            opts=opts,
        )

        # Set up SNS as the target for the CloudWatch Event Rule
        self.event_target = aws.cloudwatch.EventTarget(
            f"{name}-spot-interruption-target",
            rule=self.event_rule.name,
            arn=self.sns_topic.arn,
            opts=opts,
        )

        # Create IAM policy for instances to publish to SNS
        self.sns_policy = aws.iam.Policy(
            f"{name}-spot-interruption-policy",
            description="Allows EC2 instances to publish to spot interruption SNS topic",
            policy=pulumi.Output.all(sns_topic_arn=self.sns_topic.arn).apply(
                lambda args: f"""{{
                    "Version": "2012-10-17",
                    "Statement": [
                        {{
                            "Effect": "Allow",
                            "Action": [
                                "sns:Publish"
                            ],
                            "Resource": "{args['sns_topic_arn']}"
                        }}
                    ]
                }}"""
            ),
            opts=opts,
        )

        # Attach policy to the instance role
        self.policy_attachment = aws.iam.RolePolicyAttachment(
            f"{name}-spot-interruption-attachment",
            role=instance_role_name,
            policy_arn=self.sns_policy.arn,
            opts=opts,
        )

        # Export the SNS topic ARN
        self.sns_topic_arn = self.sns_topic.arn
        pulumi.export(f"{name}_spot_interruption_sns_topic_arn", self.sns_topic_arn)
