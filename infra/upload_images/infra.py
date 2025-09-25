import json
import os

import pulumi
import pulumi_aws as aws
from dynamo_db import dynamodb_table
from lambda_layer import dynamo_layer, label_layer, upload_layer
from pulumi import (
    AssetArchive,
    ComponentResource,
    Config,
    FileAsset,
    Output,
    ResourceOptions,
)
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.lambda_ import Function, FunctionEnvironmentArgs
from pulumi_aws.s3 import Bucket
from pulumi_aws.sfn import StateMachine
from pulumi_aws.ecr import (
    Repository as EcrRepository,
    RepositoryImageScanningConfigurationArgs as EcrRepoScanArgs,
    get_authorization_token_output as ecr_get_auth_token,
)
import pulumi_docker_build as docker_build
from pulumi_aws.sqs import Queue

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
pinecone_api_key = config.require_secret("PINECONE_API_KEY")
pinecone_index_name = config.require("PINECONE_INDEX_NAME")
pinecone_host = config.require("PINECONE_HOST")
validate_receipt_lambda_arn_cfg = config.get("VALIDATE_RECEIPT_LAMBDA_ARN")
google_places_api_key = config.require_secret("GOOGLE_PLACES_API_KEY")

code = AssetArchive(
    {
        "lambda.py": FileAsset(
            os.path.join(os.path.dirname(__file__), "lambda.py")
        )
    }
)
stack = pulumi.get_stack()

BASE_DOMAIN = "tylernorlund.com"

# For "prod" => upload.tylernorlund.com
# otherwise   => dev-upload.tylernorlund.com
if stack == "prod":
    api_domain_name = f"upload.{BASE_DOMAIN}"
else:
    api_domain_name = f"{stack}-upload.{BASE_DOMAIN}"


class UploadImages(ComponentResource):
    def __init__(
        self,
        name: str,
        raw_bucket: Bucket,
        site_bucket: Bucket,
        chromadb_bucket_name: pulumi.Input[str] | None = None,
        vpc_subnet_ids: pulumi.Input[list[str]] | None = None,
        security_group_id: pulumi.Input[str] | None = None,
        chroma_http_endpoint: pulumi.Input[str] | None = None,
        ecs_cluster_arn: pulumi.Input[str] | None = None,
        ecs_service_arn: pulumi.Input[str] | None = None,
        nat_instance_id: pulumi.Input[str] | None = None,
        base_image_ref: pulumi.Input[str] | None = None,
        opts: ResourceOptions = None,
    ):
        super().__init__(
            f"{__name__}-{name}",
            "aws:stepfunctions:UploadImages",
            {},
            opts,
        )

        # Create S3 bucket for image files
        image_bucket = Bucket(
            f"{name}-image-bucket",
            force_destroy=True,
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Dedicated artifacts bucket for NDJSON receipts (do not use site bucket)
        artifacts_bucket = Bucket(
            f"{name}-artifacts-bucket",
            force_destroy=True,
            tags={"environment": stack, "purpose": "ndjson-artifacts"},
            opts=ResourceOptions(parent=self),
        )

        # Configure CORS as a separate resource
        image_bucket_cors = aws.s3.BucketCorsConfiguration(
            f"{name}-image-bucket-cors",
            bucket=image_bucket.id,
            cors_rules=[
                aws.s3.BucketCorsConfigurationCorsRuleArgs(
                    allowed_methods=["PUT"],
                    allowed_origins=[
                        "http://localhost:3000",
                        "https://tylernorlund.com",
                        "https://dev.tylernorlund.com",
                    ],
                    allowed_headers=["*"],
                    expose_headers=["ETag"],
                    max_age_seconds=3000,
                )
            ],
            opts=ResourceOptions(parent=self),
        )

        # Create SQS queue for OCR results
        self.ocr_queue = Queue(
            f"{name}-ocr-queue",
            name=f"{name}-{stack}-ocr-queue",
            visibility_timeout_seconds=3600,
            message_retention_seconds=1209600,  # 14 days
            receive_wait_time_seconds=0,  # Short polling
            redrive_policy=None,  # No DLQ for now
            tags={
                "Purpose": "OCR Job Queue",
                "Component": name,
                "Environment": pulumi.get_stack(),
            },
            opts=ResourceOptions(parent=self),
        )

        # Create a second SQS queue for OCR JSON results (for Lambda trigger)
        self.ocr_results_queue = Queue(
            f"{name}-ocr-results-queue",
            name=f"{name}-{stack}-ocr-results-queue",
            visibility_timeout_seconds=300,
            message_retention_seconds=345600,  # 4 days
            receive_wait_time_seconds=0,  # Short polling
            redrive_policy=None,  # No DLQ for now
            tags={
                "Purpose": "OCR Results Processing",
                "Component": name,
                "Environment": pulumi.get_stack(),
            },
            opts=ResourceOptions(parent=self),
        )

        # --- Combined upload_receipt Lambda (presign + job record) ---

        upload_receipt_role = Role(
            f"{name}-upload-receipt-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # S3 access for presign
        RolePolicyAttachment(
            f"{name}-upload-receipt-s3-policy",
            role=upload_receipt_role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonS3FullAccess",
            opts=ResourceOptions(parent=self),
        )

        # Basic execution
        RolePolicyAttachment(
            f"{name}-upload-receipt-basic-exec",
            role=upload_receipt_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # Inline policy for DynamoDB + SQS (mirrors previous submit_job perms)
        RolePolicy(
            f"{name}-upload-receipt-inline",
            role=upload_receipt_role.id,
            policy=Output.all(
                dynamodb_table.name,
                self.ocr_queue.arn,
                image_bucket.arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:Query",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": f"arn:aws:dynamodb:*:*:table/{args[0]}*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": "sqs:SendMessage",
                                "Resource": args[1],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:HeadObject",
                                    "s3:PutObject",
                                    "s3:GetObject",
                                ],
                                "Resource": [args[2], args[2] + "/*"],
                            },
                        ],
                    }
                )
            ),
        )

        upload_receipt_lambda = Function(
            f"{name}-upload-receipt-lambda",
            name=f"{name}-{stack}-upload-receipt",
            role=upload_receipt_role.arn,
            runtime="python3.12",
            handler="upload_receipt.handler",
            code=AssetArchive(
                {
                    "upload_receipt.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__), "upload_receipt.py"
                        )
                    )
                }
            ),
            architectures=["arm64"],
            layers=[
                upload_layer.arn
            ],  # receipt-upload includes receipt-dynamo
            tags={"environment": stack},
            environment=FunctionEnvironmentArgs(
                variables={
                    "BUCKET_NAME": image_bucket.bucket,
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                    "OCR_JOB_QUEUE_URL": self.ocr_queue.url,
                }
            ),
            opts=ResourceOptions(parent=self, ignore_changes=["layers"]),
        )

        # Create a dedicated role for the OCR processing Lambda
        process_ocr_role = Role(
            f"{name}-process-ocr-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Attach the basic execution policy
        RolePolicyAttachment(
            f"{name}-process-ocr-basic-exec",
            role=process_ocr_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # Attach the SQS queue execution policy for event mapping
        RolePolicyAttachment(
            f"{name}-process-ocr-sqs-exec",
            role=process_ocr_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # Add DynamoDB access for the process OCR Lambda
        RolePolicy(
            f"{name}-process-ocr-dynamo-policy",
            role=process_ocr_role.id,
            policy=Output.all(
                dynamodb_table.name,
                raw_bucket.arn,
                site_bucket.arn,
                image_bucket.arn,
                self.ocr_queue.arn,
                artifacts_bucket.arn,
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:Query",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": f"arn:aws:dynamodb:*:*:table/{args[0]}*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:PutObject",
                                    "s3:HeadObject",
                                ],
                                "Resource": [
                                    args[1] + "/*",  # raw_bucket
                                    args[2] + "/*",  # site_bucket
                                    args[3] + "/*",  # image_bucket
                                    args[5] + "/*",  # artifacts_bucket
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": "sqs:SendMessage",
                                "Resource": args[4],  # ocr_queue.arn
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Begin: HTTP API Gateway v2 (replaces API Gateway v1)
        api = aws.apigatewayv2.Api(
            f"{name}-http-api",
            protocol_type="HTTP",
            cors_configuration=aws.apigatewayv2.ApiCorsConfigurationArgs(
                allow_origins=[
                    "http://localhost:3000",
                    "https://tylernorlund.com",
                    "https://www.tylernorlund.com",
                    "https://dev.tylernorlund.com",
                    "http://192.168.4.117:3000",
                ],
                allow_methods=["GET", "POST"],
                allow_headers=["Content-Type"],
                expose_headers=["Content-Type"],
                allow_credentials=True,
                max_age=3600,
            ),
            opts=ResourceOptions(parent=self),
        )

        upload_int = aws.apigatewayv2.Integration(
            f"{name}-upload-integration",
            api_id=api.id,
            integration_type="AWS_PROXY",
            integration_uri=upload_receipt_lambda.invoke_arn,
            integration_method="POST",
            payload_format_version="2.0",
            opts=ResourceOptions(parent=self),
        )

        upload_route = aws.apigatewayv2.Route(
            f"{name}-upload-route",
            api_id=api.id,
            route_key="POST /upload-receipt",
            target=upload_int.id.apply(lambda id: f"integrations/{id}"),
            opts=ResourceOptions(parent=self),
        )

        upload_permission = aws.lambda_.Permission(
            f"{name}-upload-permission",
            action="lambda:InvokeFunction",
            function=upload_receipt_lambda.name,
            principal="apigateway.amazonaws.com",
            source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
            opts=ResourceOptions(parent=self),
        )

        # (process_ocr_results Lambda defined later with complete environment)

        # ---------------------------------------------
        # Post-upload: Embed from NDJSON State Machine
        # Steps:
        # 1) ValidateReceipt (container lambda from merchant validation module)
        # 2) EmbedFromNdjson (new lambda in this module)
        # ---------------------------------------------
        # Create execution role for Step Functions
        sfn_role = aws.iam.Role(
            f"{name}-post-upload-sfn-role",
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
            opts=ResourceOptions(parent=self),
        )

        # EmbedFromNdjson Lambda role
        embed_role = Role(
            f"{name}-embed-from-ndjson-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Basic execution and VPC access (if needed by layers)
        RolePolicyAttachment(
            f"{name}-embed-basic-exec",
            role=embed_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )
        # Required for VPC-enabled Lambdas to manage ENIs and reach NAT
        RolePolicyAttachment(
            f"{name}-embed-vpc-access",
            role=embed_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        # S3 read for artifacts bucket and write to CHROMADB bucket (passed in)
        RolePolicy(
            f"{name}-embed-s3-policy",
            role=embed_role.id,
            policy=Output.all(
                artifacts_bucket.arn,
                chromadb_bucket_name or "",
            ).apply(
                lambda args: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["s3:GetObject", "s3:HeadObject"],
                                "Resource": [args[0] + "/*"],
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:PutObject",
                                    "s3:AbortMultipartUpload",
                                    "s3:CreateMultipartUpload",
                                    "s3:ListMultipartUploadParts",
                                ],
                                "Resource": (
                                    [
                                        f"arn:aws:s3:::{args[1]}",
                                        f"arn:aws:s3:::{args[1]}/*",
                                    ]
                                    if args[1]
                                    else "*"
                                ),
                            },
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # DynamoDB access for CompactionRun writes
        RolePolicy(
            f"{name}-embed-dynamo-policy",
            role=embed_role.id,
            policy=dynamodb_table.name.apply(
                lambda table_name: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:DescribeTable",
                                    "dynamodb:GetItem",
                                    "dynamodb:BatchGetItem",
                                    "dynamodb:Query",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:BatchWriteItem",
                                ],
                                "Resource": f"arn:aws:dynamodb:*:*:table/{table_name}*",
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Build container image for embed worker (needs larger deps, Chroma delta tooling)
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        embed_container_dir = os.path.join(
            os.path.dirname(__file__), "container"
        )

        embed_ecr_repo = EcrRepository(
            f"{name}-embed-repo",
            image_scanning_configuration=EcrRepoScanArgs(scan_on_push=True),
            force_delete=True,
            opts=ResourceOptions(parent=self),
        )
        ecr_auth = ecr_get_auth_token()
        resolved_base_image = (
            base_image_ref
            or os.environ.get("LABEL_BASE_IMAGE")
            or "public.ecr.aws/lambda/python:3.12"
        )

        embed_image = docker_build.Image(
            f"{name}-embed-image",
            context={"location": repo_root},
            dockerfile={
                "location": os.path.join(embed_container_dir, "Dockerfile")
            },
            platforms=["linux/arm64"],
            push=True,
            registries=[
                {
                    "address": embed_ecr_repo.repository_url.apply(
                        lambda u: u.split("/")[0]
                    ),
                    "username": ecr_auth.user_name,
                    "password": ecr_auth.password,
                }
            ],
            build_args={
                "BASE_IMAGE": resolved_base_image,
            },
            tags=[
                embed_ecr_repo.repository_url.apply(lambda u: f"{u}:latest")
            ],
            opts=ResourceOptions(parent=self, depends_on=[embed_ecr_repo]),
        )

        embed_from_ndjson_lambda = Function(
            f"{name}-embed-from-ndjson",
            name=f"{name}-{stack}-embed-from-ndjson",
            role=embed_role.arn,
            package_type="Image",
            image_uri=pulumi.Output.all(
                embed_ecr_repo.repository_url, embed_image.digest
            ).apply(lambda args: f"{args[0].split(':')[0]}@{args[1]}"),
            architectures=["arm64"],
            timeout=900,
            memory_size=1024,
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                    "CHROMADB_BUCKET": chromadb_bucket_name or "",
                    "OPENAI_API_KEY": openai_api_key,
                    "GOOGLE_PLACES_API_KEY": google_places_api_key,
                    "CHROMA_HTTP_ENDPOINT": chroma_http_endpoint or "",
                }
            ),
            vpc_config=(
                aws.lambda_.FunctionVpcConfigArgs(
                    subnet_ids=vpc_subnet_ids,
                    security_group_ids=(
                        [security_group_id] if security_group_id else None
                    ),
                )
                if vpc_subnet_ids and security_group_id
                else None
            ),
            opts=ResourceOptions(parent=self),
        )

        # Queue to batch NDJSON embedding tasks
        self.embed_ndjson_queue = Queue(
            f"{name}-embed-ndjson-queue",
            name=f"{name}-{stack}-embed-ndjson-queue",
            visibility_timeout_seconds=1200,
            message_retention_seconds=1209600,
            receive_wait_time_seconds=0,
            tags={
                "Purpose": "NDJSON Embedding Queue",
                "Component": name,
                "Environment": pulumi.get_stack(),
            },
            opts=ResourceOptions(parent=self),
        )

        # Batch launcher Lambda to start a Step Function execution per batch
        launcher_role = Role(
            f"{name}-embed-launcher-role",
            assume_role_policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
            opts=ResourceOptions(parent=self),
        )
        RolePolicyAttachment(
            f"{name}-embed-launcher-basic-exec",
            role=launcher_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        launcher_code = AssetArchive(
            {
                "embed_batch_launcher.py": FileAsset(
                    os.path.join(
                        os.path.dirname(__file__), "embed_batch_launcher.py"
                    )
                )
            }
        )

        # Step Functions role for ECS + Lambda invoke
        sfn_role = aws.iam.Role(
            f"{name}-embed-sfn-role",
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
            opts=ResourceOptions(parent=self),
        )
        # Optional: Chroma wait Lambda to ensure HTTP server is healthy before embedding
        wait_lambda = None
        if chroma_http_endpoint and ecs_cluster_arn and ecs_service_arn:
            handler_dir = os.path.join(
                os.path.dirname(__file__), "..", "chroma", "wait_handler"
            )
            wait_lambda = aws.lambda_.Function(
                f"{name}-chroma-wait",
                name=f"{name}-{stack}-chroma-wait",
                role=embed_role.arn,
                runtime="python3.12",
                architectures=["arm64"],
                handler="index.lambda_handler",
                code=pulumi.FileArchive(str(handler_dir)),
                timeout=180,
                memory_size=256,
                environment=FunctionEnvironmentArgs(
                    variables={
                        "CHROMA_HTTP_ENDPOINT": chroma_http_endpoint or "",
                        "ECS_CLUSTER_ARN": ecs_cluster_arn,
                        "ECS_SERVICE_ARN": ecs_service_arn,
                        "WAIT_TIMEOUT_SECONDS": "120",
                        "WAIT_INTERVAL_SECONDS": "5",
                    }
                ),
                vpc_config=(
                    aws.lambda_.FunctionVpcConfigArgs(
                        subnet_ids=vpc_subnet_ids,
                        security_group_ids=(
                            [security_group_id] if security_group_id else None
                        ),
                    )
                    if vpc_subnet_ids and security_group_id
                    else None
                ),
                opts=ResourceOptions(parent=self),
            )
        # Allow invoking embed_from_ndjson and (optionally) waiting lambda
        RolePolicy(
            f"{name}-embed-sfn-invoke",
            role=sfn_role.id,
            policy=embed_from_ndjson_lambda.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["lambda:InvokeFunction"],
                                "Resource": [arn],
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )
        if wait_lambda is not None:
            RolePolicy(
                f"{name}-embed-sfn-wait-invoke",
                role=sfn_role.id,
                policy=wait_lambda.arn.apply(
                    lambda arn: json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": ["lambda:InvokeFunction"],
                                    "Resource": [arn],
                                }
                            ],
                        }
                    )
                ),
                opts=ResourceOptions(parent=self),
            )
        # ECS scale permissions if provided
        if ecs_cluster_arn and ecs_service_arn:
            RolePolicy(
                f"{name}-embed-sfn-ecs",
                role=sfn_role.id,
                policy=Output.all(ecs_cluster_arn, ecs_service_arn).apply(
                    lambda args: json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": [
                                        "ecs:UpdateService",
                                        "ecs:DescribeServices",
                                    ],
                                    "Resource": [args[1]],
                                }
                            ],
                        }
                    )
                ),
                opts=ResourceOptions(parent=self),
            )

        # Define State Machine: scale up → map embed → scale down
        embed_sfn = StateMachine(
            f"{name}-embed-from-ndjson-sm",
            role_arn=sfn_role.arn,
            definition=Output.all(
                embed_from_ndjson_lambda.arn,
                chroma_http_endpoint or Output.from_input(""),
                ecs_cluster_arn or Output.from_input(""),
                ecs_service_arn or Output.from_input(""),
                wait_lambda.arn if wait_lambda else Output.from_input(""),
            ).apply(
                lambda xs: json.dumps(
                    {
                        "StartAt": (
                            "ScaleUpChroma"
                            if xs[2] and xs[3]
                            else "ForEachItem"
                        ),
                        "States": {
                            **(
                                {
                                    "ScaleUpChroma": {
                                        "Type": "Task",
                                        "Resource": "arn:aws:states:::aws-sdk:ecs:updateService",
                                        "Parameters": {
                                            "Cluster": xs[2],
                                            "Service": xs[3],
                                            "DesiredCount": 1,
                                        },
                                        "ResultPath": "$.scaleUp",
                                        "Next": "AwaitChromaTasks",
                                    },
                                    "AwaitChromaTasks": {
                                        "Type": "Task",
                                        "Resource": "arn:aws:states:::aws-sdk:ecs:describeServices",
                                        "Parameters": {
                                            "Cluster": xs[2],
                                            "Services": [xs[3]],
                                        },
                                        "ResultSelector": {
                                            "runningCount.$": "$.Services[0].RunningCount",
                                        },
                                        "ResultPath": "$.svc",
                                        "Next": "ChromaTasksReady?",
                                    },
                                    "ChromaTasksReady?": {
                                        "Type": "Choice",
                                        "Choices": [
                                            {
                                                "Variable": "$.svc.runningCount",
                                                "NumericGreaterThanEquals": 1,
                                                "Next": (
                                                    "WaitForChromaHealthy"
                                                    if xs[4]
                                                    else "ForEachItem"
                                                ),
                                            }
                                        ],
                                        "Default": "WaitChromaDelay",
                                    },
                                    "WaitChromaDelay": {
                                        "Type": "Wait",
                                        "Seconds": 5,
                                        "Next": "AwaitChromaTasks",
                                    },
                                    **(
                                        {
                                            "WaitForChromaHealthy": {
                                                "Type": "Task",
                                                "Resource": "arn:aws:states:::lambda:invoke",
                                                "Parameters": {
                                                    "FunctionName": xs[4]
                                                },
                                                "ResultPath": "$.chromaHealth",
                                                "Next": "ForEachItem",
                                            }
                                        }
                                        if xs[4]
                                        else {}
                                    ),
                                }
                                if xs[2] and xs[3]
                                else {}
                            ),
                            "ForEachItem": {
                                "Type": "Map",
                                "ItemsPath": "$.items",
                                "MaxConcurrency": 10,
                                "ResultPath": "$.embedResults",
                                "Iterator": {
                                    "StartAt": "EmbedFromNdjson",
                                    "States": {
                                        "EmbedFromNdjson": {
                                            "Type": "Task",
                                            "Resource": xs[0],
                                            "End": True,
                                        }
                                    },
                                },
                                "Next": (
                                    "ScaleDownChroma"
                                    if xs[2] and xs[3]
                                    else "Done"
                                ),
                            },
                            **(
                                {
                                    "ScaleDownChroma": {
                                        "Type": "Task",
                                        "Resource": "arn:aws:states:::aws-sdk:ecs:updateService",
                                        "Parameters": {
                                            "Cluster": xs[2],
                                            "Service": xs[3],
                                            "DesiredCount": 0,
                                        },
                                        "ResultPath": "$.scaleDown",
                                        "Next": "Done",
                                    }
                                }
                                if xs[2] and xs[3]
                                else {}
                            ),
                            "Done": {"Type": "Succeed"},
                        },
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Launcher Lambda can StartExecution of the state machine
        RolePolicy(
            f"{name}-embed-launcher-sfn-start",
            role=launcher_role.id,
            policy=embed_sfn.arn.apply(
                lambda arn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["states:StartExecution"],
                                "Resource": arn,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        embed_batch_launcher = Function(
            f"{name}-embed-batch-launcher",
            name=f"{name}-{stack}-embed-batch-launcher",
            role=launcher_role.arn,
            runtime="python3.12",
            handler="embed_batch_launcher.handler",
            code=launcher_code,
            architectures=["arm64"],
            timeout=60,
            memory_size=256,
            environment=FunctionEnvironmentArgs(
                variables={
                    "EMBED_SFN_ARN": embed_sfn.arn,
                }
            ),
            opts=ResourceOptions(parent=self),
        )

        # Wire SQS -> launcher (not directly to worker)
        RolePolicyAttachment(
            f"{name}-embed-launcher-sqs-exec",
            role=launcher_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole",
            opts=ResourceOptions(parent=self),
        )
        aws.lambda_.EventSourceMapping(
            f"{name}-embed-ndjson-launcher-mapping",
            event_source_arn=self.embed_ndjson_queue.arn,
            function_name=embed_batch_launcher.name,
            batch_size=10,
            maximum_batching_window_in_seconds=10,
            enabled=True,
            opts=ResourceOptions(parent=self),
        )

        # Allow process_ocr Lambda to enqueue embedding jobs
        RolePolicy(
            f"{name}-process-ocr-embed-sqs-send",
            role=process_ocr_role.id,
            policy=self.embed_ndjson_queue.arn.apply(
                lambda qarn: json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["sqs:SendMessage"],
                                "Resource": qarn,
                            }
                        ],
                    }
                )
            ),
            opts=ResourceOptions(parent=self),
        )

        # Create process_ocr Lambda with full environment (unique logical name)
        process_ocr_lambda = Function(
            f"{name}-process-ocr-results-lambda",
            name=f"{name}-{stack}-process-ocr-results",
            role=process_ocr_role.arn,
            runtime="python3.12",
            handler="process_ocr_results.handler",
            code=AssetArchive(
                {
                    "process_ocr_results.py": FileAsset(
                        os.path.join(
                            os.path.dirname(__file__), "process_ocr_results.py"
                        )
                    )
                }
            ),
            environment=FunctionEnvironmentArgs(
                variables={
                    "DYNAMO_TABLE_NAME": dynamodb_table.name,
                    "S3_BUCKET": image_bucket.bucket,
                    "RAW_BUCKET": raw_bucket.bucket,
                    "SITE_BUCKET": site_bucket.bucket,
                    "ARTIFACTS_BUCKET": artifacts_bucket.bucket,
                    "OCR_JOB_QUEUE_URL": self.ocr_queue.url,
                    "OCR_RESULTS_QUEUE_URL": self.ocr_results_queue.url,
                    "EMBED_NDJSON_QUEUE_URL": self.embed_ndjson_queue.url,
                }
            ),
            tags={"environment": stack},
            timeout=300,
            memory_size=1024,
            architectures=["arm64"],
            layers=[label_layer.arn, upload_layer.arn],
            opts=ResourceOptions(parent=self, ignore_changes=["layers"]),
        )

        # Adopt existing mapping if already present to avoid ResourceConflict
        existing_mapping_uuid = pulumi.Config("portfolio").get(
            "ocr_results_mapping_uuid"
        )
        aws.lambda_.EventSourceMapping(
            f"{name}-ocr-results-mapping",
            event_source_arn=self.ocr_results_queue.arn,
            function_name=process_ocr_lambda.name,
            batch_size=10,
            enabled=True,
            opts=ResourceOptions(
                parent=self,
                import_=(
                    existing_mapping_uuid if existing_mapping_uuid else None
                ),
            ),
        )

        # Remove Step Function embedding path in favor of SQS batching

        log_group = aws.cloudwatch.LogGroup(
            f"{name}-api-gw-log-group",
            name=api.id.apply(
                lambda id: f"API-Gateway-Execution-Logs_{id}_default"
            ),
            retention_in_days=14,
            opts=ResourceOptions(parent=self),
        )

        stage = aws.apigatewayv2.Stage(
            f"{name}-http-stage",
            api_id=api.id,
            name="$default",
            auto_deploy=True,
            default_route_settings=aws.apigatewayv2.StageDefaultRouteSettingsArgs(
                throttling_burst_limit=1000,
                throttling_rate_limit=2000,
                detailed_metrics_enabled=True,
            ),
            access_log_settings=aws.apigatewayv2.StageAccessLogSettingsArgs(
                destination_arn=log_group.arn,
                format='{"requestId":"$context.requestId","ip":"$context.identity.sourceIp","caller":"$context.identity.caller","user":"$context.identity.user","requestTime":"$context.requestTime","httpMethod":"$context.httpMethod","resourcePath":"$context.resourcePath","status":"$context.status","protocol":"$context.protocol","responseLength":"$context.responseLength"}',
            ),
            opts=ResourceOptions(parent=self),
        )

        # ACM certificate for custom domain
        hosted_zone = aws.route53.get_zone(name=BASE_DOMAIN)
        cert = aws.acm.Certificate(
            f"{name}-upload-cert",
            domain_name=api_domain_name,
            validation_method="DNS",
            opts=ResourceOptions(parent=self),
        )
        cert_validation_options = cert.domain_validation_options[0]
        cert_validation_record = aws.route53.Record(
            f"{name}-upload-cert-dns",
            zone_id=hosted_zone.zone_id,
            name=cert_validation_options.resource_record_name,
            type=cert_validation_options.resource_record_type,
            records=[cert_validation_options.resource_record_value],
            ttl=60,
            opts=ResourceOptions(parent=self),
        )
        cert_validation = aws.acm.CertificateValidation(
            f"{name}-upload-cert-validation",
            certificate_arn=cert.arn,
            validation_record_fqdns=[cert_validation_record.fqdn],
            opts=ResourceOptions(parent=self),
        )

        # API Gateway v2 custom domain
        custom_domain = aws.apigatewayv2.DomainName(
            f"{name}-upload-domain",
            domain_name=api_domain_name,
            domain_name_configuration=aws.apigatewayv2.DomainNameDomainNameConfigurationArgs(
                certificate_arn=cert_validation.certificate_arn,
                endpoint_type="REGIONAL",
                security_policy="TLS_1_2",
            ),
            opts=ResourceOptions(parent=self, depends_on=[cert_validation]),
        )

        api_mapping = aws.apigatewayv2.ApiMapping(
            f"{name}-upload-api-mapping",
            api_id=api.id,
            domain_name=custom_domain.id,
            stage=stage.id,
            opts=ResourceOptions(parent=self),
        )

        # Route53 alias record for the custom domain
        alias_record = aws.route53.Record(
            f"{name}-upload-alias-record",
            zone_id=hosted_zone.zone_id,
            name=api_domain_name,
            type="A",
            aliases=[
                {
                    "name": custom_domain.domain_name_configuration.target_domain_name,
                    "zone_id": custom_domain.domain_name_configuration.hosted_zone_id,
                    "evaluateTargetHealth": True,
                }
            ],
            opts=ResourceOptions(parent=self),
        )

        self.endpoint_url = pulumi.Output.concat("https://", api_domain_name)
