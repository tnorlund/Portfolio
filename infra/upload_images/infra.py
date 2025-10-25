import json
import os
from pathlib import Path

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
# StateMachine import removed - no longer using Step Functions
from pulumi_aws.ecr import (
    Repository as EcrRepository,
    RepositoryImageScanningConfigurationArgs as EcrRepoScanArgs,
)
from pulumi_aws.sqs import Queue

# Import the CodeBuildDockerImage component
from codebuild_docker_image import CodeBuildDockerImage

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
        embed_ndjson_queue_url: pulumi.Input[str] | None = None,
        vpc_subnet_ids: pulumi.Input[list[str]] | None = None,
        security_group_id: pulumi.Input[str] | None = None,
        chroma_http_endpoint: pulumi.Input[str] | None = None,
        ecs_cluster_arn: pulumi.Input[str] | None = None,
        ecs_service_arn: pulumi.Input[str] | None = None,
        nat_instance_id: pulumi.Input[str] | None = None,
        efs_access_point_arn: pulumi.Input[str] | None = None,
        opts: ResourceOptions = None,
    ):
        super().__init__(
            f"{__name__}-{name}",
            "aws:lambda:UploadImages",
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
            visibility_timeout_seconds=900,  # Must be >= Lambda timeout (600s for container-based process_ocr)
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
        
        # Store embed_ndjson_queue_url for use in Lambda environment
        # If not provided, we'll use the internal queue created below
        self._external_embed_ndjson_queue_url = embed_ndjson_queue_url

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

        # Attach VPC access policy if VPC is configured (required for EFS)
        if vpc_subnet_ids and security_group_id:
            RolePolicyAttachment(
                f"{name}-process-ocr-vpc-exec",
                role=process_ocr_role.name,
                policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole",
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
                pulumi.Output.from_input(chromadb_bucket_name),
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
                                    f"arn:aws:s3:::{args[6]}/*" if args[6] else None,  # chromadb_bucket
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": "s3:ListBucket",
                                "Resource": f"arn:aws:s3:::{args[6]}" if args[6] else None,
                            },
                            {
                                "Effect": "Allow",
                                "Action": "sqs:SendMessage",
                                "Resource": args[4],  # ocr_queue.arn
                            },
                        ],
                    }
                ) if args[6] else json.dumps(
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
        # Step Function removed - container Lambda handles everything directly
        # The process_ocr_results container Lambda now handles:
        # - OCR processing
        # - Merchant validation  
        # - Embedding creation
        # - S3 delta upload
        # - Compaction trigger
        # ---------------------------------------------

        # Step Function removed - no longer need embed_ndjson_queue
        # The container Lambda handles everything directly

        # Create container-based process_ocr Lambda with merchant validation
        # This replaces the old zip-based Lambda and integrates merchant validation + embedding
        process_ocr_lambda_config = {
            "role_arn": process_ocr_role.arn,
            "timeout": 600,  # 10 minutes (longer for merchant validation + embedding)
            "memory_size": 2048,  # More memory for ChromaDB operations
            "environment": {
                "DYNAMO_TABLE_NAME": dynamodb_table.name,
                "S3_BUCKET": image_bucket.bucket,
                "RAW_BUCKET": raw_bucket.bucket,
                "SITE_BUCKET": site_bucket.bucket,
                "ARTIFACTS_BUCKET": artifacts_bucket.bucket,
                "OCR_JOB_QUEUE_URL": self.ocr_queue.url,
                "OCR_RESULTS_QUEUE_URL": self.ocr_results_queue.url,
                "CHROMADB_BUCKET": chromadb_bucket_name,
                "CHROMA_HTTP_ENDPOINT": chroma_http_endpoint,
                "GOOGLE_PLACES_API_KEY": google_places_api_key,
                "OPENAI_API_KEY": openai_api_key,
                # EFS configuration for ChromaDB read-only access
                "CHROMA_ROOT": "/mnt/chroma" if efs_access_point_arn else "/tmp/chroma",
                "CHROMADB_STORAGE_MODE": "auto",  # Use EFS if available, fallback to S3
            },
            "vpc_config": {
                "subnet_ids": vpc_subnet_ids,
                "security_group_ids": [security_group_id],
            } if vpc_subnet_ids and security_group_id else None,
            "file_system_config": {
                "arn": efs_access_point_arn,
                "local_mount_path": "/mnt/chroma",
            } if efs_access_point_arn else None,
            }
        
        # Use CodeBuildDockerImage for AWS-based builds
        process_ocr_docker_image = CodeBuildDockerImage(
            f"{name}-process-ocr-image",
            dockerfile_path="infra/upload_images/container_ocr/Dockerfile",
            build_context_path=".",  # Project root for monorepo access
            source_paths=["receipt_upload"],  # Include receipt_upload package (only needed for this Lambda)
            # lambda_function_name=f"{name}-{stack}-process-ocr-results",  # Let Pulumi manage Lambda config
            lambda_config=process_ocr_lambda_config,
            platform="linux/arm64",
            opts=ResourceOptions(parent=self, depends_on=[process_ocr_role]),
        )

        # Use the Lambda function created by CodeBuildDockerImage
        process_ocr_lambda = process_ocr_docker_image.lambda_function

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
