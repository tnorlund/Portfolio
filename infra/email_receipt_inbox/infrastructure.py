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

        # Deterministic SES physical names so the bucket policy can pin the
        # exact receipt rule allowed to write under raw/ (confused-deputy guard).
        rule_set_name = f"{name}-{stack}"
        store_rule_name = f"{name}-store-{stack}"
        store_rule_arn = (f"arn:aws:ses:{region}:{account_id}:"
                          f"receipt-rule-set/{rule_set_name}:"
                          f"receipt-rule/{store_rule_name}")

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
        # The inbound MX record is published LAST (see end of __init__): once it
        # exists, SES can accept mail, so everything a delivered message needs
        # (bucket versioning, the S3->Lambda notification, an active rule set)
        # must already be in place or the first message is lost/unversioned.

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
        versioning = aws.s3.BucketVersioning(
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
                # Noncurrent-version expiration is meaningless until versioning
                # is Enabled; order it after so the rule isn't applied to an
                # unversioned bucket.
                opts=ResourceOptions(parent=self, depends_on=[versioning]))
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
                        # Scope to this account AND the specific receipt rule, so
                        # no other SES rule in the account can write under raw/.
                        "Condition": {"StringEquals": {
                            "aws:SourceAccount": a[1],
                            "aws:SourceArn": store_rule_arn}},
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
        self.dlq = aws.sqs.Queue(
            f"{name}-parser-dlq",
            message_retention_seconds=1209600,  # 14 days
            tags=tags, opts=child)
        dlq = self.dlq
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
            # Public ingress: cap this function's slice of the account's shared
            # concurrency so a mail flood cannot starve every other Lambda.
            # Throttled async S3 invokes retry; any that still exhaust the
            # retry/age window land in the DLQ (alarmed below), and the raw
            # email persists in S3 under raw/ independent of the async trigger,
            # so it can be redriven from raw/ — the cap loses no mail.
            reserved_concurrent_executions=10,
            code=pulumi.AssetArchive({
                ".": pulumi.FileArchive(LAMBDA_DIR),
            }),
            tags=tags,
            # The function must not exist (and thus be invokable) before its
            # execution-role policies are attached, or early invokes fail
            # AccessDenied on GetObject/PutObject.
            opts=ResourceOptions(parent=self,
                                 depends_on=[s3_policy, logs_attach]))
        # Route async invokes that exhaust retries to the DLQ. Bound the retry
        # window so a persistently failing event drains to the DLQ within the
        # hour instead of retrying against the 6h default.
        async_config = aws.lambda_.FunctionEventInvokeConfig(
            f"{name}-parser-async",
            function_name=self.parser.name,
            maximum_retry_attempts=2,
            maximum_event_age_in_seconds=3600,
            destination_config={"on_failure": {"destination": dlq.arn}},
            opts=child)
        # The DLQ is the sole capture point for async invokes that exhaust
        # retries or the age window under a sustained flood. Alarm on its depth
        # so those failures surface for redrive-from-raw/ instead of silently
        # aging out at the 14-day retention.
        aws.cloudwatch.MetricAlarm(
            f"{name}-parser-dlq-depth",
            comparison_operator="GreaterThanThreshold",
            evaluation_periods=1,
            metric_name="ApproximateNumberOfMessagesVisible",
            namespace="AWS/SQS",
            period=300,
            statistic="Maximum",
            threshold=0,
            alarm_description=(
                "Async parser invokes are landing in the DLQ; redrive from "
                "raw/ before the 14-day message retention expires."),
            dimensions={"QueueName": dlq.name},
            treat_missing_data="notBreaching",
            tags=tags,
            opts=child)
        invoke_perm = aws.lambda_.Permission(
            f"{name}-parser-s3-invoke",
            action="lambda:InvokeFunction",
            function=self.parser.name,
            principal="s3.amazonaws.com",
            source_arn=self.bucket.arn,
            # S3 bucket ARNs carry no account id, so pin the owning account too:
            # if the bucket name were reclaimed in another account after deletion,
            # that account's bucket could not invoke this function.
            source_account=account_id,
            opts=child)
        notification = aws.s3.BucketNotification(
            f"{name}-mail-notify",
            bucket=self.bucket.id,
            lambda_functions=[{
                "lambda_function_arn": self.parser.arn,
                "events": ["s3:ObjectCreated:*"],
                "filter_prefix": "raw/",
            }],
            # S3 validates it can invoke the target at create time, so the
            # invoke permission must already exist. Also gate on the async
            # config so the DLQ failure destination is in place before any
            # delivered message can invoke the parser — otherwise an early
            # failure exhausts the default retries and is silently discarded.
            # activation and the MX record both depend on this notification, so
            # this dependency propagates to the whole mail-accepting graph.
            opts=ResourceOptions(
                parent=self,
                depends_on=[self.parser, invoke_perm, async_config]))

        # --- receipt rule set
        rule_set = aws.ses.ReceiptRuleSet(
            f"{name}-rules", rule_set_name=rule_set_name, opts=child)
        store_rule = aws.ses.ReceiptRule(
            f"{name}-store-rule",
            name=store_rule_name,
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
        activation = None
        if activate:
            # Activate only after the rule exists (never briefly publish an
            # empty active set) AND after the bucket pipeline is ready, so the
            # first stored message is versioned and triggers the parser.
            activation = aws.ses.ActiveReceiptRuleSet(
                f"{name}-rules-active",
                rule_set_name=rule_set.rule_set_name,
                opts=ResourceOptions(
                    parent=self,
                    depends_on=[store_rule, versioning, notification]))

        # --- inbound MX, published LAST. Once this resolves SES starts
        # accepting mail; gating it on the active rule set plus versioning +
        # notification means no delivered message can arrive before the
        # storage/trigger graph exists.
        #
        # Only publish MX once this rule set is actually the one SES will
        # evaluate. SES has a single active receipt rule set per account+region,
        # and this component's store rule lives ONLY inside the rule set it
        # creates here — it is never merged into a foreign set. So the sole way
        # this recipient's mail gets stored is if we activate our own set. If we
        # did not (``activate=False``), publishing MX would route mail to
        # whatever OTHER set is active — which has no rule for this recipient —
        # so the message is rejected or dropped instead of stored. Therefore MX
        # is published only when we activated our set (``activation is not
        # None``), with the MX record depending on that activation.
        if activation is not None:
            aws.route53.Record(
                f"{name}-mx",
                zone_id=zone.zone_id,
                name=domain,
                type="MX",
                ttl=300,
                records=[f"10 inbound-smtp.{region}.amazonaws.com"],
                opts=ResourceOptions(
                    parent=self,
                    depends_on=[store_rule, versioning, notification,
                                activation]))

        self.register_outputs({
            "address": self.address,
            "bucket": self.bucket.bucket,
            "parser_arn": self.parser.arn,
            "dlq_url": self.dlq.url,
        })
