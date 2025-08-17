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
from pulumi_aws.sqs import Queue

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
pinecone_api_key = config.require_secret("PINECONE_API_KEY")
pinecone_index_name = config.require("PINECONE_INDEX_NAME")
pinecone_host = config.require("PINECONE_HOST")

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
                                ],
                            },
                            {
                                "Effect": "Allow",
                                "Action": "sqs:SendMessage",
                                "Resource": args[
                                    4
                                ],  # ocr_queue.arn (now args[4])
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

        process_ocr_lambda = Function(
            f"{name}-process-ocr-results-lambda",
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
                    "OCR_JOB_QUEUE_URL": self.ocr_queue.url,
                    "OCR_RESULTS_QUEUE_URL": self.ocr_results_queue.url,
                }
            ),
            tags={"environment": stack},
            timeout=300,  # 5 minutes
            memory_size=1024,  # 1GB
            architectures=["arm64"],
            layers=[
                label_layer.arn,
                upload_layer.arn,
            ],  # Both include receipt-dynamo
            opts=ResourceOptions(parent=self, ignore_changes=["layers"]),
        )

        aws.lambda_.EventSourceMapping(
            f"{name}-ocr-results-event-mapping",
            event_source_arn=self.ocr_results_queue.arn,
            function_name=process_ocr_lambda.name,
            batch_size=10,
            enabled=True,
            opts=ResourceOptions(parent=self),
        )

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
