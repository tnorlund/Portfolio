"""
LayoutLM Training Infrastructure Component.

Provides EC2-based training infrastructure with optional custom AMI support.
When using a custom AMI (built by ami_builder.py), startup is ~10 seconds
instead of 5-10 minutes.
"""

import base64
import json
import string
import textwrap
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, Output, ResourceOptions


class LayoutLMTrainingInfra(ComponentResource):
    """
    EC2-based training infrastructure for LayoutLM.

    Args:
        name: Resource name prefix
        dynamodb_table_name: DynamoDB table for training data
        ami_ssm_param: SSM parameter containing custom AMI ID (from ami_builder)
        ami_id: Direct AMI ID override (takes precedence over ami_ssm_param)
        key_name: EC2 key pair name for SSH access
        instance_type: EC2 instance type (default: g5.xlarge)
        opts: Pulumi resource options
    """

    def __init__(
        self,
        name: str,
        dynamodb_table_name: Output[str] | str,
        *,
        ami_ssm_param: Optional[str] = None,
        ami_id: Optional[str] = None,
        key_name: str = "Nov2025MacBookPro",
        instance_type: str = "g5.xlarge",
        opts: ResourceOptions | None = None,
    ) -> None:
        super().__init__("custom:ml:LayoutLMTrainingInfra", name, None, opts)

        stack = pulumi.get_stack()

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

        # Managed policies
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
        subnets = aws.ec2.get_subnets(
            filters=[
                aws.ec2.GetSubnetsFilterArgs(
                    name="vpc-id", values=[default_vpc.id]
                )
            ]
        )

        # Security Group for SSH access
        self.ssh_sg = aws.ec2.SecurityGroup(
            f"{name}-ssh-sg",
            description="Allow SSH",
            vpc_id=default_vpc.id,
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=22,
                    to_port=22,
                    cidr_blocks=["0.0.0.0/0"],
                    description="SSH access",
                )
            ],
            egress=[
                aws.ec2.SecurityGroupEgressArgs(
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                    cidr_blocks=["0.0.0.0/0"],
                )
            ],
            tags={"Component": name},
            opts=ResourceOptions(parent=self),
        )

        # Determine AMI ID and whether we're using a custom AMI
        # This must be computed BEFORE user data generation because SSM lookup may fail
        if ami_id:
            # Direct AMI ID provided
            resolved_ami_id = ami_id
            self._use_custom_ami = True
            pulumi.log.info(f"Using provided AMI ID: {ami_id}")
        elif ami_ssm_param:
            # Look up AMI ID from SSM parameter
            try:
                ssm_param = aws.ssm.get_parameter(name=ami_ssm_param)
                resolved_ami_id = ssm_param.value
                self._use_custom_ami = True
                pulumi.log.info(
                    f"Using AMI from SSM param {ami_ssm_param}: {resolved_ami_id}"
                )
            except Exception:
                # SSM param doesn't exist yet, fall back to base AMI
                # IMPORTANT: Use full user data since base AMI has no pre-installed deps
                pulumi.log.warn(
                    f"SSM param {ami_ssm_param} not found, using base AMI with full setup. "
                    "Run `pulumi up` again after AMI build completes."
                )
                resolved_ami_id = aws.ec2.get_ami(
                    most_recent=True,
                    owners=["amazon"],
                    filters=[
                        {"name": "name", "values": ["Deep Learning * AMI GPU PyTorch*"]},
                        {"name": "architecture", "values": ["x86_64"]},
                    ],
                ).id
                self._use_custom_ami = False
        else:
            # No custom AMI, use base Deep Learning AMI
            resolved_ami_id = aws.ec2.get_ami(
                most_recent=True,
                owners=["amazon"],
                filters=[
                    {"name": "name", "values": ["Deep Learning * AMI GPU PyTorch*"]},
                    {"name": "architecture", "values": ["x86_64"]},
                ],
            ).id
            self._use_custom_ami = False

        # Generate user data based on whether we're using custom AMI
        if self._use_custom_ami:
            user_data = Output.all(
                bucket=self.bucket.bucket,
                table=Output.from_input(dynamodb_table_name),
                queue=self.queue.url,
            ).apply(
                lambda args: self._fast_user_data(
                    bucket=args["bucket"],
                    table=args["table"],
                    queue=args["queue"],
                )
            )
            pulumi.log.info("Using custom AMI with fast startup (~10 seconds)")
        else:
            user_data = Output.all(
                bucket=self.bucket.bucket,
                table=Output.from_input(dynamodb_table_name),
                queue=self.queue.url,
            ).apply(
                lambda args: self._full_user_data(
                    bucket=args["bucket"],
                    table=args["table"],
                    queue=args["queue"],
                )
            )
            pulumi.log.info("Using base AMI with full setup (~5-10 minutes)")

        # Base64-encode user data for Launch Template
        user_data_b64 = user_data.apply(
            lambda s: base64.b64encode(s.encode("utf-8")).decode("utf-8")
        )

        # Launch template
        self.launch_template = aws.ec2.LaunchTemplate(
            f"{name}-lt",
            image_id=resolved_ami_id,
            instance_type=instance_type,
            key_name=key_name,
            iam_instance_profile=aws.ec2.LaunchTemplateIamInstanceProfileArgs(
                name=self.instance_profile.name
            ),
            network_interfaces=[
                aws.ec2.LaunchTemplateNetworkInterfaceArgs(
                    associate_public_ip_address=True,
                    security_groups=[self.ssh_sg.id],
                )
            ],
            user_data=user_data_b64,
            opts=ResourceOptions(parent=self),
        )

        # AutoScaling Group
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
            opts=ResourceOptions(
                parent=self, ignore_changes=["desired_capacity"]
            ),
        )

        # Outputs
        self.register_outputs(
            {
                "bucket_name": self.bucket.bucket,
                "queue_url": self.queue.url,
                "instance_profile": self.instance_profile.name,
                "asg_name": self.asg.name,
                "using_custom_ami": str(self._use_custom_ami),
            }
        )

    def _fast_user_data(self, bucket: str, table: str, queue: str) -> str:
        """
        Minimal user data for custom AMI.

        Only downloads latest wheels and starts worker - all dependencies
        are pre-installed in the AMI.
        """
        return textwrap.dedent(f"""
            #!/bin/bash
            set -euxo pipefail
            exec > /var/log/user-data.log 2>&1

            echo "=== Fast startup with custom AMI ==="
            date

            # Set region
            REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region || echo "us-east-1")
            export AWS_DEFAULT_REGION=$REGION

            # Activate conda environment
            export LD_LIBRARY_PATH="${{LD_LIBRARY_PATH:-}}"
            source /opt/conda/bin/activate pytorch

            # Source environment variables
            source /etc/profile.d/layoutlm.sh || true

            # Download latest wheels (your code updates)
            echo "Downloading latest wheels..."
            aws s3 cp s3://{bucket}/wheels/ /opt/wheels/ --recursive || true

            # Install wheels if present
            DYNAMO_WHEEL=$(ls -t /opt/wheels/receipt_dynamo-*.whl 2>/dev/null | head -n1 || true)
            LAYOUTLM_WHEEL=$(ls -t /opt/wheels/receipt_layoutlm-*.whl 2>/dev/null | head -n1 || true)
            if [ -n "$DYNAMO_WHEEL" ]; then
                echo "Installing $DYNAMO_WHEEL"
                pip install --force-reinstall "$DYNAMO_WHEEL"
            fi
            if [ -n "$LAYOUTLM_WHEEL" ]; then
                echo "Installing $LAYOUTLM_WHEEL"
                pip install --force-reinstall "$LAYOUTLM_WHEEL"
            fi

            # Create worker script
            cat >/opt/worker.py <<'PY'
import json, os, time
import boto3

queue_url = os.environ['TRAINING_QUEUE_URL']
table = os.environ['DYNAMO_TABLE_NAME']
sqs = boto3.client('sqs')

print(f"Worker started. Queue: {{queue_url}}, Table: {{table}}")

while True:
    resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=10)
    msgs = resp.get('Messages', [])
    if not msgs:
        time.sleep(15)
        continue
    m = msgs[0]
    body = json.loads(m['Body']) if m.get('Body') else {{}}
    print('Received job:', body)
    os.system(f"layoutlm-cli train --job-name trial-{{int(time.time())}} --dynamo-table {{table}} --epochs 1 --batch-size 2 || true")
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m['ReceiptHandle'])
PY

            # Set environment and start worker
            export DYNAMO_TABLE_NAME="{table}"
            export TRAINING_QUEUE_URL="{queue}"
            nohup python3 /opt/worker.py >/var/log/worker.log 2>&1 &

            echo "=== Startup complete ==="
            date
        """).strip()

    def _full_user_data(self, bucket: str, table: str, queue: str) -> str:
        """
        Full user data for base AMI.

        Installs all dependencies from scratch - takes 5-10 minutes.
        Used as fallback when custom AMI is not available.
        """
        tmpl = textwrap.dedent(
            """#!/bin/bash
            set -euxo pipefail
            exec > /var/log/user-data.log 2>&1

            echo "=== Full setup with base AMI ==="
            date

            # Install packages
            if command -v apt-get &>/dev/null; then
                apt-get update -y || true
                apt-get install -y awscli curl tar gzip || true
            elif command -v yum &>/dev/null; then
                yum update -y || true
                yum install -y awscli curl tar gzip || true
            fi

            # Set region
            REGION=$$(curl -s http://169.254.169.254/latest/meta-data/placement/region || echo "us-east-1")
            export AWS_DEFAULT_REGION=$$REGION

            # Activate conda
            export LD_LIBRARY_PATH="$${LD_LIBRARY_PATH:-}"
            set +u
            source /opt/conda/bin/activate pytorch
            set -u
            python -m pip install --upgrade pip setuptools wheel

            # Install dependencies
            export PIP_ONLY_BINARY=:all:
            python -m pip install -U pyarrow==16.1.0 sentencepiece==0.1.99
            python -m pip install -U \
              'transformers>=4.40.0' \
              'datasets>=2.19.0' \
              'boto3>=1.34.0' \
              pillow tqdm filelock 'pydantic>=2.10.6' \
              'accelerate>=0.21.0' \
              seqeval backports.tarfile

            # Environment setup
            echo 'export TORCH_ALLOW_TF32=1' >> /etc/profile.d/layoutlm.sh
            echo 'export TOKENIZERS_PARALLELISM=false' >> /etc/profile.d/layoutlm.sh
            echo 'export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True' >> /etc/profile.d/layoutlm.sh
            echo 'export CUDA_DEVICE_MAX_CONNECTIONS=1' >> /etc/profile.d/layoutlm.sh
            chmod 0755 /etc/profile.d/layoutlm.sh

            # Download and install wheels
            if id "ubuntu" &>/dev/null; then
                DEFAULT_USER="ubuntu"
            elif id "ec2-user" &>/dev/null; then
                DEFAULT_USER="ec2-user"
            else
                DEFAULT_USER=$$(getent passwd | awk -F: '$$3 >= 1000 && $$1 != "nobody" {print $$1; exit}')
            fi
            install -d -m 0775 -o $$DEFAULT_USER -g $$DEFAULT_USER /opt/wheels
            /usr/bin/aws s3 cp s3://${bucket}/wheels/ /opt/wheels/ --recursive || true
            chown -R $$DEFAULT_USER:$$DEFAULT_USER /opt/wheels || true
            chmod -R a+r /opt/wheels || true
            DYNAMO_WHEEL=$$(ls -t /opt/wheels/receipt_dynamo-*.whl 2>/dev/null | head -n1 || true)
            LAYOUTLM_WHEEL=$$(ls -t /opt/wheels/receipt_layoutlm-*.whl 2>/dev/null | head -n1 || true)
            if [ -n "$$DYNAMO_WHEEL" ]; then pip install "$$DYNAMO_WHEEL"; fi
            if [ -n "$$LAYOUTLM_WHEEL" ]; then pip install "$$LAYOUTLM_WHEEL"; fi

            # Create worker
            export HF_DATASETS_DISABLE_MULTIPROCESSING=1
            export TOKENIZERS_PARALLELISM=false
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
    os.system(f"layoutlm-cli train --job-name trial-{int(time.time())} --dynamo-table {table} --epochs 1 --batch-size 2 || true")
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m['ReceiptHandle'])
PY
            DYNAMO_TABLE_NAME="${table}"
            TRAINING_QUEUE_URL="${queue}"
            export DYNAMO_TABLE_NAME TRAINING_QUEUE_URL
            nohup python3 /opt/worker.py >/var/log/worker.log 2>&1 &

            echo "=== Setup complete ==="
            date
            """
        )
        return string.Template(tmpl).substitute(
            bucket=bucket,
            table=table,
            queue=queue,
        )
