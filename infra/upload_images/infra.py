import os
import json
import pulumi
from pulumi import (
    ComponentResource,
    Output,
    ResourceOptions,
    Config,
    FileAsset,
    AssetArchive,
)
import pulumi_aws as aws
from pulumi_aws.sfn import StateMachine
from pulumi_aws.s3 import Bucket
from pulumi_aws.lambda_ import Function, FunctionEnvironmentArgs
from pulumi_aws.iam import Role, RolePolicy, RolePolicyAttachment
from pulumi_aws.sqs import Queue
from dynamo_db import dynamodb_table
from lambda_layer import dynamo_layer, label_layer, upload_layer

config = Config("portfolio")
openai_api_key = config.require_secret("OPENAI_API_KEY")
pinecone_api_key = config.require_secret("PINECONE_API_KEY")
pinecone_index_name = config.require("PINECONE_INDEX_NAME")
pinecone_host = config.require("PINECONE_HOST")

code = AssetArchive(
    {"lambda.py": FileAsset(os.path.join(os.path.dirname(__file__), "lambda.py"))}
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
            acl="private",
            force_destroy=True,
            cors_rules=[
                aws.s3.BucketCorsRuleArgs(
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
            tags={"environment": stack},
            opts=ResourceOptions(parent=self),
        )

        # Create SQS queue for OCR results
        self.ocr_queue = Queue(
            f"{name}-ocr-queue",
            visibility_timeout_seconds=3600,
            opts=ResourceOptions(parent=self),
        )

        # Create a second SQS queue for OCR JSON results (for Lambda trigger)
        self.ocr_results_queue = Queue(
            f"{name}-ocr-results-queue",
            visibility_timeout_seconds=300,
            opts=ResourceOptions(parent=self),
        )

        get_presigned_url_role = Role(
            f"{name}-get-presigned-url-role",
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
            f"{name}-get-presigned-url-policy",
            role=get_presigned_url_role.name,
            policy_arn="arn:aws:iam::aws:policy/AmazonS3FullAccess",
            opts=ResourceOptions(parent=self),
        )

        RolePolicyAttachment(
            f"{name}-get-presigned-url-basic-exec",
            role=get_presigned_url_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
            opts=ResourceOptions(parent=self),
        )

        get_presigned_url_lambda = Function(
            f"{name}-get-presigned-url-lambda",
            role=get_presigned_url_role.arn,
            runtime="python3.12",
            handler="get_presigned_url.handler",
            code=AssetArchive(
                {
                    "get_presigned_url.py": FileAsset(
                        os.path.join(os.path.dirname(__file__), "get_presigned_url.py")
                    ),
                }
            ),
            environment=FunctionEnvironmentArgs(
                variables={
                    "BUCKET_NAME": image_bucket.bucket,
                }
            ),
            opts=ResourceOptions(parent=self, ignore_changes=["layers"]),
        )

        # Define the environment variables for the lambda
        env_vars = FunctionEnvironmentArgs(
            variables={
                "DYNAMO_TABLE_NAME": dynamodb_table.name,
                "OPENAI_API_KEY": openai_api_key,
                "PINECONE_API_KEY": pinecone_api_key,
                "PINECONE_INDEX_NAME": pinecone_index_name,
                "PINECONE_HOST": pinecone_host,
                "S3_BUCKET": image_bucket.bucket,
                "RAW_BUCKET": raw_bucket.bucket,
                "SITE_BUCKET": site_bucket.bucket,
                "OCR_JOB_QUEUE_URL": self.ocr_queue.url,
                "OCR_RESULTS_QUEUE_URL": self.ocr_results_queue.url,
                "MAX_BATCH_TIMEOUT": 60,
            },
        )

        submit_job_lambda_role = Role(
            f"{name}-submit-job-lambda-role",
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
            f"{name}-lambda-basic-execution",
            role=submit_job_lambda_role.name,
            policy_arn=(
                "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
            ),
        )

        # Custom inline policy for DynamoDB access, SQS SendMessage, and S3 HeadObject, PutObject, and GetObject
        RolePolicy(
            f"{name}-lambda-dynamo-policy",
            role=submit_job_lambda_role.id,
            policy=Output.all(
                dynamodb_table.name,
                self.ocr_queue.arn,
                self.ocr_results_queue.arn,
                image_bucket.arn,
                raw_bucket.arn,
                site_bucket.arn,
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
                                "Resource": args[1],  # OCR job queue
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "sqs:ReceiveMessage",
                                    "sqs:DeleteMessage",
                                    "sqs:GetQueueAttributes",
                                ],
                                "Resource": args[2],  # OCR results queue
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:HeadObject",
                                    "s3:PutObject",
                                    "s3:GetObject",
                                ],
                                "Resource": [
                                    args[3],  # image bucket
                                    args[3] + "/*",
                                    args[4],  # raw bucket
                                    args[4] + "/*",
                                    args[5],  # site bucket
                                    args[5] + "/*",
                                ],
                            },
                        ],
                    }
                )
            ),
        )

        submit_job_lambda = Function(
            f"{name}-submit-job-lambda",
            role=submit_job_lambda_role.arn,
            runtime="python3.12",
            handler="submit_job.handler",
            code=AssetArchive(
                {
                    "submit_job.py": FileAsset(
                        os.path.join(os.path.dirname(__file__), "submit_job.py")
                    )
                }
            ),
            layers=[dynamo_layer.arn, label_layer.arn, upload_layer.arn],
            tags={"environment": stack},
            environment=env_vars,
            opts=ResourceOptions(parent=self, ignore_changes=["layers"]),
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

        integration = aws.apigatewayv2.Integration(
            f"{name}-http-integration",
            api_id=api.id,
            integration_type="AWS_PROXY",
            integration_uri=get_presigned_url_lambda.invoke_arn,
            integration_method="POST",
            payload_format_version="2.0",
            opts=ResourceOptions(parent=self),
        )

        route = aws.apigatewayv2.Route(
            f"{name}-http-route",
            api_id=api.id,
            route_key="GET /get-presigned-url",
            target=integration.id.apply(lambda id: f"integrations/{id}"),
            opts=ResourceOptions(parent=self),
        )

        lambda_permission = aws.lambda_.Permission(
            f"{name}-http-lambda-permission",
            action="lambda:InvokeFunction",
            function=get_presigned_url_lambda.name,
            principal="apigateway.amazonaws.com",
            source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
            opts=ResourceOptions(parent=self),
        )

        # --- Add POST /submit-job integration, route, and permission ---
        submit_job_integration = aws.apigatewayv2.Integration(
            f"{name}-submit-job-integration",
            api_id=api.id,
            integration_type="AWS_PROXY",
            integration_uri=submit_job_lambda.invoke_arn,
            integration_method="POST",
            payload_format_version="2.0",
            opts=ResourceOptions(parent=self),
        )

        submit_job_route = aws.apigatewayv2.Route(
            f"{name}-submit-job-route",
            api_id=api.id,
            route_key="POST /submit-job",
            target=submit_job_integration.id.apply(lambda id: f"integrations/{id}"),
            opts=ResourceOptions(parent=self),
        )

        submit_job_lambda_permission = aws.lambda_.Permission(
            f"{name}-submit-job-lambda-permission",
            action="lambda:InvokeFunction",
            function=submit_job_lambda.name,
            principal="apigateway.amazonaws.com",
            source_arn=api.execution_arn.apply(lambda arn: f"{arn}/*/*"),
            opts=ResourceOptions(parent=self),
        )

        process_ocr_lambda = Function(
            f"{name}-process-ocr-results-lambda",
            role=submit_job_lambda_role.arn,
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
            environment=env_vars,
            tags={"environment": stack},
            timeout=300,  # 5 minutes
            memory_size=1024,  # 1GB
            layers=[dynamo_layer.arn, label_layer.arn, upload_layer.arn],
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
            name=api.id.apply(lambda id: f"API-Gateway-Execution-Logs_{id}_default"),
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
