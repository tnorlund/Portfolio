import pulumi
import pulumi_aws as aws
from dynamo_db import dynamodb_table
from s3_website import site_bucket
from dynamo_db import dynamodb_table

region = aws.config.region
caller_identity = aws.get_caller_identity()
account_id = caller_identity.account_id
pulumi_stack_name = pulumi.get_stack()
ecr_image_uri = (
    f"{account_id}.dkr.ecr.{region}.amazonaws.com/cluster-ocr:{pulumi_stack_name}"
)

bucket = aws.s3.Bucket("raw-image-bucket")
pulumi.export("image_bucket_name", bucket.bucket)


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
    memory_size=512,  # Memory size is in MB (megabytes)
    timeout=60 * 3,  # 3 minutes
    environment={
        "variables": {
            "DYNAMO_DB_TABLE": dynamodb_table.name,
            "S3_BUCKET": bucket.bucket,
            "CDN_S3_BUCKET": site_bucket.bucket,
            "CDN_PATH": "assets/",
        }
    },
)

pulumi.export("CDN_PATH", "assets/")
pulumi.export("cluster_lambda_function_name", lambda_function.name)
