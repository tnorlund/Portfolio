"""SES inbound email pipeline for receipt ingestion.

receipts@<subdomain> -> SES receipt rule -> S3 (raw/) -> Lambda parser
-> S3 (parsed/ JSON). The private reconciliation plane consumes parsed/.

DNS (MX + DKIM CNAMEs) is created on an isolated subdomain so the root
domain's mail posture is untouched.

CAUTION: SES allows ONE active receipt rule set per account+region.
``activate=True`` claims it; safe on an account with no prior SES receiving,
but review before enabling anywhere SES receiving already exists.
"""
from __future__ import annotations

import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions

LAMBDA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lambdas")


class EmailReceiptInbox(ComponentResource):
    """Inbound receipt-email pipeline: SES -> S3 -> parser Lambda -> S3."""

    def __init__(
        self,
        name: str,
        zone_name: str = "tylernorlund.com",
        subdomain: str = "in",
        recipient_localpart: str = "receipts",
        activate: bool = True,
        raw_retention_days: Optional[int] = None,
        tags: Optional[dict[str, str]] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__("portfolio:infra:EmailReceiptInbox", name, None, opts)
        stack = pulumi.get_stack()
        child = ResourceOptions(parent=self)
        region = aws.get_region().region
        account_id = aws.get_caller_identity().account_id
        tags = {"Environment": stack, "Component": "email-receipt-inbox",
                **(tags or {})}

        domain = f"{subdomain}.{zone_name}"
        self.address = f"{recipient_localpart}@{domain}"
        zone = aws.route53.get_zone(name=zone_name)

        # --- SES identity + DKIM + inbound MX on the isolated subdomain
        identity = aws.ses.DomainIdentity(f"{name}-identity", domain=domain,
                                          opts=child)
        # Publish the domain-verification TXT so SES can actually verify the
        # identity (unverified identities silently reject inbound mail). Record
        # lives under the isolated ``in.`` subdomain — the root zone is untouched.
        verification_record = aws.route53.Record(
            f"{name}-verify",
            zone_id=zone.zone_id,
            name=f"_amazonses.{domain}",
            type="TXT",
            ttl=600,
            records=[identity.verification_token],
            opts=child)
        # Block dependent resources until SES observes the record and marks the
        # identity verified.
        verification = aws.ses.DomainIdentityVerification(
            f"{name}-identity-verified",
            domain=identity.id,
            opts=ResourceOptions(parent=self, depends_on=[verification_record]))
        dkim = aws.ses.DomainDkim(f"{name}-dkim", domain=identity.domain,
                                  opts=child)
        for i in range(3):
            token = dkim.dkim_tokens[i]
            aws.route53.Record(
                f"{name}-dkim-{i}",
                zone_id=zone.zone_id,
                name=token.apply(lambda t: f"{t}._domainkey.{domain}"),
                type="CNAME",
                ttl=300,
                records=[token.apply(lambda t: f"{t}.dkim.amazonses.com")],
                opts=child)
        aws.route53.Record(
            f"{name}-mx",
            zone_id=zone.zone_id,
            name=domain,
            type="MX",
            ttl=300,
            records=[f"10 inbound-smtp.{region}.amazonaws.com"],
            opts=child)

        # --- raw + parsed mail bucket
        self.bucket = aws.s3.Bucket(
            f"{name}-mail",
            bucket=f"{name}-mail-{stack}-{account_id}",
            tags=tags,
            opts=child)
        aws.s3.BucketPublicAccessBlock(
            f"{name}-mail-pab",
            bucket=self.bucket.id,
            block_public_acls=True, block_public_policy=True,
            ignore_public_acls=True, restrict_public_buckets=True,
            opts=child)
        aws.s3.BucketServerSideEncryptionConfiguration(
            f"{name}-mail-sse",
            bucket=self.bucket.id,
            rules=[{"apply_server_side_encryption_by_default": {
                "sse_algorithm": "AES256"}}],
            opts=child)
        # Versioning gives replays/overwrites an immutable lineage: the handler
        # fetches the exact event version rather than "latest" (see handler.py).
        aws.s3.BucketVersioning(
            f"{name}-mail-versioning",
            bucket=self.bucket.id,
            versioning_configuration={"status": "Enabled"},
            opts=child)
        if raw_retention_days:
            # Expire both prefixes on the same clock; with versioning enabled,
            # also expire noncurrent versions so replays don't accumulate.
            expire = {"days": raw_retention_days}
            noncurrent = {"noncurrent_days": raw_retention_days}
            aws.s3.BucketLifecycleConfiguration(
                f"{name}-mail-lifecycle",
                bucket=self.bucket.id,
                rules=[
                    {"id": "expire-raw", "status": "Enabled",
                     "filter": {"prefix": "raw/"},
                     "expiration": expire,
                     "noncurrent_version_expiration": noncurrent},
                    {"id": "expire-parsed", "status": "Enabled",
                     "filter": {"prefix": "parsed/"},
                     "expiration": expire,
                     "noncurrent_version_expiration": noncurrent},
                ],
                opts=child)
        bucket_policy = aws.s3.BucketPolicy(
            f"{name}-mail-ses-policy",
            bucket=self.bucket.id,
            policy=pulumi.Output.all(self.bucket.arn, account_id).apply(
                lambda a: pulumi.Output.json_dumps({
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Sid": "AllowSESPuts",
                        "Effect": "Allow",
                        "Principal": {"Service": "ses.amazonaws.com"},
                        "Action": "s3:PutObject",
                        "Resource": f"{a[0]}/raw/*",
                        "Condition": {"StringEquals": {
                            "aws:SourceAccount": a[1]}},
                    }],
                })),
            opts=child)

        # --- parser Lambda
        role = aws.iam.Role(
            f"{name}-parser-role",
            assume_role_policy=pulumi.Output.json_dumps({
                "Version": "2012-10-17",
                "Statement": [{"Action": "sts:AssumeRole",
                               "Effect": "Allow",
                               "Principal": {"Service": "lambda.amazonaws.com"}}],
            }),
            tags=tags, opts=child)
        logs_attach = aws.iam.RolePolicyAttachment(
            f"{name}-parser-logs",
            role=role.name,
            policy_arn=aws.iam.ManagedPolicy.AWS_LAMBDA_BASIC_EXECUTION_ROLE,
            opts=child)
        # --- durable backstop: async S3 invokes that exhaust Lambda retries
        # land in a DLQ instead of being silently discarded (see FunctionEvent
        # InvokeConfig below).
        dlq = aws.sqs.Queue(
            f"{name}-parser-dlq",
            message_retention_seconds=1209600,  # 14 days
            tags=tags, opts=child)
        s3_policy = aws.iam.RolePolicy(
            f"{name}-parser-s3",
            role=role.id,
            policy=pulumi.Output.all(self.bucket.arn, dlq.arn).apply(
                lambda a: pulumi.Output.json_dumps({
                    "Version": "2012-10-17",
                    "Statement": [
                        {"Effect": "Allow",
                         "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                         "Resource": f"{a[0]}/raw/*"},
                        {"Effect": "Allow", "Action": ["s3:PutObject"],
                         "Resource": f"{a[0]}/parsed/*"},
                        {"Effect": "Allow", "Action": ["sqs:SendMessage"],
                         "Resource": a[1]},
                    ],
                })),
            opts=child)
        self.parser = aws.lambda_.Function(
            f"{name}-parser",
            runtime="python3.12",
            handler="handler.lambda_handler",
            role=role.arn,
            timeout=60,
            memory_size=256,
            code=pulumi.AssetArchive({
                ".": pulumi.FileArchive(LAMBDA_DIR),
            }),
            tags=tags,
            # The function must not exist (and thus be invokable) before its
            # execution-role policies are attached, or early invokes fail
            # AccessDenied on GetObject/PutObject.
            opts=ResourceOptions(parent=self,
                                 depends_on=[s3_policy, logs_attach]))
        # Route async invokes that exhaust retries to the DLQ.
        aws.lambda_.FunctionEventInvokeConfig(
            f"{name}-parser-async",
            function_name=self.parser.name,
            maximum_retry_attempts=2,
            destination_config={"on_failure": {"destination": dlq.arn}},
            opts=child)
        invoke_perm = aws.lambda_.Permission(
            f"{name}-parser-s3-invoke",
            action="lambda:InvokeFunction",
            function=self.parser.name,
            principal="s3.amazonaws.com",
            source_arn=self.bucket.arn,
            opts=child)
        aws.s3.BucketNotification(
            f"{name}-mail-notify",
            bucket=self.bucket.id,
            lambda_functions=[{
                "lambda_function_arn": self.parser.arn,
                "events": ["s3:ObjectCreated:*"],
                "filter_prefix": "raw/",
            }],
            # S3 validates it can invoke the target at create time, so the
            # invoke permission must already exist.
            opts=ResourceOptions(parent=self,
                                 depends_on=[self.parser, invoke_perm]))

        # --- receipt rule set
        rule_set = aws.ses.ReceiptRuleSet(
            f"{name}-rules", rule_set_name=f"{name}-{stack}", opts=child)
        store_rule = aws.ses.ReceiptRule(
            f"{name}-store-rule",
            rule_set_name=rule_set.rule_set_name,
            recipients=[self.address],
            enabled=True,
            scan_enabled=True,
            # Financial mail: reject plaintext delivery rather than default to
            # opportunistic (Optional) TLS.
            tls_policy="Require",
            s3_actions=[{
                "bucket_name": self.bucket.bucket,
                "object_key_prefix": "raw/",
                "position": 1,
            }],
            # SES validates at create time that (a) the identity is verified and
            # (b) it can write to the bucket, so both must precede the rule.
            opts=ResourceOptions(
                parent=self,
                depends_on=[rule_set, verification, bucket_policy]))
        if activate:
            # Activate only after the rule exists, so we never briefly publish
            # an empty active rule set.
            aws.ses.ActiveReceiptRuleSet(
                f"{name}-rules-active",
                rule_set_name=rule_set.rule_set_name,
                opts=ResourceOptions(parent=self,
                                     depends_on=[store_rule]))

        self.register_outputs({
            "address": self.address,
            "bucket": self.bucket.bucket,
            "parser_arn": self.parser.arn,
        })
