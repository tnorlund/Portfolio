import json
import textwrap
import string
import base64

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
        subnets = aws.ec2.get_subnets(
            filters=[
                aws.ec2.GetSubnetsFilterArgs(
                    name="vpc-id", values=[default_vpc.id]
                )
            ]
        )

        # Security Group for SSH access (open; simplify for connectivity)
        ssh_cidr = "0.0.0.0/0"

        self.ssh_sg = aws.ec2.SecurityGroup(
            f"{name}-ssh-sg",
            description="Allow SSH",
            vpc_id=default_vpc.id,
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp",
                    from_port=22,
                    to_port=22,
                    cidr_blocks=[ssh_cidr],
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

        # User data worker (placeholder that can install wheel if uploaded)
        def _user_data(variables: dict[str, str]) -> str:
            tmpl = textwrap.dedent(
                """#!/bin/bash
                set -euxo pipefail
                # Log all output to cloud-init output and our own log file
                exec > /var/log/user-data.log 2>&1
                yum update -y || true
                yum install -y awscli curl tar gzip || true

                # Set region for AWS CLI from instance metadata
                REGION=$$(curl -s http://169.254.169.254/latest/meta-data/placement/region || echo "us-east-1")
                export AWS_DEFAULT_REGION=$$REGION

                # Use DLAMI's preinstalled Conda environment (Python 3.10 + Torch)
                # Conda activation scripts reference LD_LIBRARY_PATH; ensure it's defined under 'set -u'
                export LD_LIBRARY_PATH="$${LD_LIBRARY_PATH:-}"
                # Temporarily disable 'nounset' while sourcing conda
                set +u
                source /opt/conda/bin/activate pytorch
                set -u
                python -m pip install --upgrade pip setuptools wheel

                # Prefer prebuilt wheels to avoid compiling heavy deps
                export PIP_ONLY_BINARY=:all:
                python -m pip install -U pyarrow==16.1.0 sentencepiece==0.1.99

                # Runtime dependencies for training and CLI
                python -m pip install -U \
                  'transformers>=4.40.0' \
                  'datasets>=2.19.0' \
                  'boto3>=1.34.0' \
                  pillow tqdm filelock 'pydantic>=2.10.6' \
                  'accelerate>=0.21.0' \
                  seqeval backports.tarfile

                # Performance env defaults
                echo 'export TORCH_ALLOW_TF32=1' >> /etc/profile.d/layoutlm.sh
                echo 'export TOKENIZERS_PARALLELISM=false' >> /etc/profile.d/layoutlm.sh
                echo 'export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True' >> /etc/profile.d/layoutlm.sh
                echo 'export CUDA_DEVICE_MAX_CONNECTIONS=1' >> /etc/profile.d/layoutlm.sh
                chmod 0755 /etc/profile.d/layoutlm.sh

                # Download any published wheels and install (dynamo first)
                install -d -m 0775 -o ec2-user -g ec2-user /opt/wheels
                /usr/bin/aws s3 cp s3://${bucket}/wheels/ /opt/wheels/ --recursive || true
                chown -R ec2-user:ec2-user /opt/wheels || true
                chmod -R a+r /opt/wheels || true
                DYNAMO_WHEEL=$$(ls -t /opt/wheels/receipt_dynamo-*.whl 2>/dev/null | head -n1 || true)
                LAYOUTLM_WHEEL=$$(ls -t /opt/wheels/receipt_layoutlm-*.whl 2>/dev/null | head -n1 || true)
                if [ -n "$$DYNAMO_WHEEL" ]; then pip install "$$DYNAMO_WHEEL"; fi
                if [ -n "$$LAYOUTLM_WHEEL" ]; then pip install "$$LAYOUTLM_WHEEL"; fi

                # Runtime knobs for small CPU instances
                export HF_DATASETS_DISABLE_MULTIPROCESSING=1
                export TOKENIZERS_PARALLELISM=false
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
    os.system(f"layoutlm-cli train --job-name trial-{int(time.time())} --dynamo-table {table} --epochs 1 --batch-size 2 || true")
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m['ReceiptHandle'])
PY
                DYNAMO_TABLE_NAME="${table}"
                TRAINING_QUEUE_URL="${queue}"
                export DYNAMO_TABLE_NAME TRAINING_QUEUE_URL
                # Run worker in background to avoid blocking cloud-init
                nohup python3 /opt/worker.py >/var/log/worker.log 2>&1 &
                """
            )
            return string.Template(tmpl).substitute(variables)

        user_data = Output.all(
            bucket=self.bucket.bucket,
            table=Output.from_input(dynamodb_table_name),
            queue=self.queue.url,
        ).apply(
            lambda args: _user_data(
                {
                    "bucket": args["bucket"],
                    "table": args["table"],
                    "queue": args["queue"],
                }
            )
        )

        # Base64-encode user data for Launch Template
        user_data_b64 = user_data.apply(
            lambda s: base64.b64encode(s.encode("utf-8")).decode("utf-8")
        )

        # Launch template
        self.launch_template = aws.ec2.LaunchTemplate(
            f"{name}-lt",
            image_id=aws.ec2.get_ami(
                most_recent=True,
                owners=["amazon"],
                filters=[
                    {
                        "name": "name",
                        "values": ["Deep Learning AMI GPU PyTorch*"],
                    },
                ],
            ).id,
            instance_type="g5.xlarge",
            key_name="training_key",
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
            opts=ResourceOptions(
                parent=self, ignore_changes=["desired_capacity"]
            ),
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
