import pulumi
import pulumi_aws as aws
from dynamo_db import dynamodb_table
from s3_website import site_bucket
from dynamo_db import dynamodb_table
import json


region = aws.config.region
caller_identity = aws.get_caller_identity()
account_id = caller_identity.account_id
pulumi_stack_name = pulumi.get_stack()
ecr_image_uri = (
    f"{account_id}.dkr.ecr.{region}.amazonaws.com/cluster-ocr:{pulumi_stack_name}"
)
config = pulumi.Config()

bucket = aws.s3.Bucket("raw-image-bucket")
pulumi.export("raw_bucket_name", bucket.bucket)
raw_bucket_policy = aws.s3.BucketPolicy(
    "raw-bucket-policy",
    bucket=bucket.bucket,
    policy=bucket.bucket.apply(
        lambda bucket_name: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    # Statement 1: Allows CloudWatch Logs to read the bucket’s ACL/location,
                    # and list the bucket. No ACL condition here.
                    {
                        "Sid": "AllowCloudWatchLogsReadBucket",
                        "Effect": "Allow",
                        "Principal": {
                            "Service": f"logs.{region}.amazonaws.com"
                        },
                        "Action": [
                            "s3:GetBucketAcl",
                            "s3:GetBucketLocation",
                            "s3:ListBucket"
                        ],
                        # For these "bucket-level" actions, the Resource is just the bucket ARN
                        "Resource": f"arn:aws:s3:::{bucket_name}"
                    },
                    # Statement 2: Allows CloudWatch Logs to put objects in the bucket (/*),
                    # with the required ACL condition.
                    {
                        "Sid": "AllowCloudWatchLogsPutObject",
                        "Effect": "Allow",
                        "Principal": {
                            "Service": f"logs.{region}.amazonaws.com"
                        },
                        "Action": "s3:PutObject",
                        # For object-level actions, use the bucket/* ARN
                        "Resource": f"arn:aws:s3:::{bucket_name}/*",
                        "Condition": {
                            "StringEquals": {
                                "s3:x-amz-acl": "bucket-owner-full-control"
                            }
                        }
                    }
                ]
            }
        )
    )
)


lambda_role = aws.iam.Role(
    "lambda-execution-role",
    assume_role_policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Effect": "Allow",
                },
            ],
        }
    ),
)

lambda_role_policy = aws.iam.RolePolicy(
    "lambda-execution-role-policy",
    role=lambda_role.name,
    policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": [
                        "s3:GetObject",
                    ],
                    "Effect": "Allow",
                    "Resource": [bucket.arn, pulumi.Output.concat(bucket.arn, "/*")],
                },
                {
                    "Action": [
                        "s3:PutObject",
                    ],
                    "Effect": "Allow",
                    "Resource": [
                        site_bucket.arn,
                        pulumi.Output.concat(site_bucket.arn, "/*"),
                        bucket.arn,
                        pulumi.Output.concat(bucket.arn, "/*"),
                    ],
                },
                {
                    "Action": [
                        "dynamodb:Query",
                        "dynamodb:DescribeTable",
                        "dynamodb:PutItem",
                        "dynamodb:BatchWriteItem",
                    ],
                    "Resource": [
                        dynamodb_table.arn,
                        pulumi.Output.concat(dynamodb_table.arn, "/index/GSI1"),
                    ],
                    "Effect": "Allow",
                },
            ],
        }
    ),
)

aws.iam.RolePolicyAttachment(
    "lambda-basic-execution-policy",
    role=lambda_role.name,
    policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

lambda_function = aws.lambda_.Function(
    "cluster-lambda",
    package_type="Image",
    image_uri=ecr_image_uri,
    role=lambda_role.arn,
    memory_size=1024,  # 1 GB
    timeout=60 * 5,  # 5 minutes
    environment={
        "variables": {
            "DYNAMO_DB_TABLE": dynamodb_table.name,
            "S3_BUCKET": bucket.bucket,
            "CDN_S3_BUCKET": site_bucket.bucket,
            "CDN_PATH": "assets/",
            "OPENAI_API_KEY": config.require_secret("OPENAI_API_KEY"),
        }
    },
    tags={
        "Name": "cluster-ocr",
        "Pulumi_Stack": pulumi_stack_name,
        "Pulumi_Project": pulumi.get_project(),
    },
)

pulumi.export("cluster_lambda_function_name", lambda_function.name)
