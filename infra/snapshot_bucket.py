import json
from typing import Optional

import pulumi
import pulumi_aws as aws


class SnapshotBucket(pulumi.ComponentResource):
    """S3 bucket for ChromaDB snapshots with versioning and lifecycle.

    - Bucket with SSE-S3
    - Versioning enabled
    - Lifecycle: delete objects after 30 days
    - Optional CORS
    - Initial object keys for snapshot structure
    - Bucket policy (placeholder principal; wire to ECS role externally)
    """

    def __init__(
        self,
        name: str,
        *,
        enable_cors: bool = True,
        opts: Optional[pulumi.ResourceOptions] = None,
    ) -> None:
        super().__init__("custom:storage:SnapshotBucket", name, {}, opts)

        self.bucket = aws.s3.Bucket(
            f"{name}-snapshots",
            server_side_encryption_configuration=aws.s3.BucketServerSideEncryptionConfigurationArgs(
                rules=[
                    aws.s3.BucketServerSideEncryptionConfigurationRuleArgs(
                        apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultArgs(
                            sse_algorithm="AES256"
                        )
                    )
                ]
            ),
            cors_configuration=(
                aws.s3.BucketCorsConfigurationArgs(
                    cors_rules=[
                        aws.s3.BucketCorsConfigurationCorsRuleArgs(
                            allowed_methods=["GET"],
                            allowed_origins=["*"],
                            allowed_headers=["*"],
                            max_age_seconds=300,
                        )
                    ]
                )
                if enable_cors
                else None
            ),
            tags={
                "Name": f"{name}-snapshots",
                "Environment": pulumi.get_stack(),
                "ManagedBy": "Pulumi",
            },
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.s3.BucketVersioningV2(
            f"{name}-versioning",
            bucket=self.bucket.id,
            versioning_configuration=aws.s3.BucketVersioningV2VersioningConfigurationArgs(
                status="Enabled"
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.s3.BucketLifecycleConfigurationV2(
            f"{name}-lifecycle",
            bucket=self.bucket.id,
            rules=[
                aws.s3.BucketLifecycleConfigurationV2RuleArgs(
                    id="expire-old-snapshots",
                    status="Enabled",
                    expiration=aws.s3.BucketLifecycleConfigurationV2RuleExpirationArgs(
                        days=30
                    ),
                )
            ],
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Initial empty structure
        for key in [
            "chromadb/snapshots/.keep",
            "chromadb/metadata/.keep",
        ]:
            aws.s3.BucketObjectv2(
                f"{name}-{key.replace('/', '-')}",
                bucket=self.bucket.bucket,
                key=key,
                content="",
                opts=pulumi.ResourceOptions(parent=self),
            )

        # Bucket policy placeholder (restrict later to specific principal)
        policy_doc = pulumi.Output.all(self.bucket.arn).apply(
            lambda args: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "s3:GetObject",
                            ],
                            # Replace with ECS task role principal in main wiring
                            "Principal": {"AWS": "*"},
                            "Resource": f"{args[0]}/*",
                        }
                    ],
                }
            )
        )

        self.bucket_policy = aws.s3.BucketPolicy(
            f"{name}-policy",
            bucket=self.bucket.id,
            policy=policy_doc,
            opts=pulumi.ResourceOptions(parent=self),
        )

        self.register_outputs({"bucket_name": self.bucket.bucket})
