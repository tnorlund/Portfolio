import json
import textwrap
import string

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions


class LayoutLMTrainingInfra(ComponentResource):
    def __init__(
        self,
        name: str,
        dynamodb_table_name: Output[str] | str,
        opts: ResourceOptions | None = None,
    ) -> None:
        super().__init__("custom:ml:LayoutLMTrainingInfra", name, None, opts)

        # S3 bucket for models/artifacts/wheels
        self.bucket = aws.s3.Bucket(
            f"{name}-models",
            force_destroy=True,
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        # SQS queue for training jobs (each message = one trial)
        self.queue = aws.sqs.Queue(
            f"{name}-training-queue",
            visibility_timeout_seconds=7200,
            message_retention_seconds=1209600,
            receive_wait_time_seconds=10,
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        # IAM role for EC2 instances
        self.role = aws.iam.Role(
            f"{name}-instance-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "ec2.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Minimal, broad managed policies for first test (tighten later)
        aws.iam.RolePolicyAttachment(
            f"{name}-cw-logs",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/CloudWatchLogsFullAccess",
            opts=ResourceOptions(parent=self),
        )
        aws.iam.RolePolicyAttachment(
            f"{name}-s3",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonS3FullAccess",
            opts=ResourceOptions(parent=self),
        )
        aws.iam.RolePolicyAttachment(
            f"{name}-dynamo",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess",
            opts=ResourceOptions(parent=self),
        )
        aws.iam.RolePolicyAttachment(
            f"{name}-sqs",
            role=self.role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonSQSFullAccess",
            opts=ResourceOptions(parent=self),
        )

        self.instance_profile = aws.iam.InstanceProfile(
            f"{name}-instance-profile",
            role=self.role.name,
            opts=ResourceOptions(parent=self),
        )

        # Discover default VPC subnets
        default_vpc = aws.ec2.get_vpc(default=True)
        subnets = aws.ec2.get_subnets(vpc_id=default_vpc.id)

        # User data worker (placeholder that can install wheel if uploaded)
        def _user_data(vars: dict[str, str]) -> str:
            tmpl = textwrap.dedent(
                """#!/bin/bash
                set -euxo pipefail
                exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1
                yum update -y || true
                yum install -y python3 python3-pip awscli || true
                python3 -m venv /opt/venv
                source /opt/venv/bin/activate
                pip install --upgrade pip
                # Try to install the wheel if present in S3
                aws s3 cp s3://$bucket/wheels/receipt_layoutlm.whl /tmp/receipt_layoutlm.whl || true
                if [ -f /tmp/receipt_layoutlm.whl ]; then
                  pip install /tmp/receipt_layoutlm.whl
                fi
                # Install runtime deps just in case
                pip install boto3
                # Minimal worker loop to prove SQS access
                cat >/opt/worker.py <<'PY'
import json, os, time
import boto3

queue_url = os.environ['TRAINING_QUEUE_URL']
table = os.environ['DYNAMO_TABLE_NAME']
sqs = boto3.client('sqs')

while True:
    resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=10)
    msgs = resp.get('Messages', [])
    if not msgs:
        time.sleep(15)
        continue
    m = msgs[0]
    body = json.loads(m['Body']) if m.get('Body') else {}
    print('Received job:', body)
    # Placeholder: run layoutlm-cli if installed
    os.system(f"layoutlm-cli train --job-name trial-{int(time.time())} --dynamo-table {table} || true")
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m['ReceiptHandle'])
PY
                DYNAMO_TABLE_NAME=$table
                TRAINING_QUEUE_URL=$queue
                export DYNAMO_TABLE_NAME TRAINING_QUEUE_URL
                python3 /opt/worker.py
                """
            )
            return string.Template(tmpl).substitute(vars)

        user_data = Output.all(
            bucket=self.bucket.bucket,
            table=Output.from_input(dynamodb_table_name),
            queue=self.queue.url,
        ).apply(
            lambda args: _user_data(
                {"bucket": args[0], "table": args[1], "queue": args[2]}
            )
        )

        # Launch template
        self.launch_template = aws.ec2.LaunchTemplate(
            f"{name}-lt",
            image_id=aws.ec2.get_ami(
                most_recent=True,
                owners=["amazon"],
                filters=[
                    {"name": "name", "values": ["amzn2-ami-hvm-*-x86_64-gp2"]},
                ],
            ).id,
            instance_type="g4dn.xlarge",
            iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
                name=self.instance_profile.name
            ),
            user_data=user_data,
            # Enable spot by setting instance market options via ASG
            opts=ResourceOptions(parent=self),
        )

        # AutoScaling Group (spot)
        self.asg = aws.autoscaling.Group(
            f"{name}-asg",
            desired_capacity=0,
            min_size=0,
            max_size=2,
            vpc_zone_identifiers=subnets.ids,
            launch_template=aws.autoscaling.GroupLaunchTemplateArgs(
                id=self.launch_template.id,
                version="$Latest",
            ),
            mixed_instances_policy=None,
            tags=[
                aws.autoscaling.GroupTagArgs(
                    key="Name",
                    value=f"{name}-trainer",
                    propagate_at_launch=True,
                )
            ],
            opts=ResourceOptions(parent=self),
        )

        # Simple output handles
        self.register_outputs(
            {
                "bucket_name": self.bucket.bucket,
                "queue_url": self.queue.url,
                "instance_profile": self.instance_profile.name,
                "asg_name": self.asg.name,
            }
        )
