"""SES inbound mail archive plus the email-receipt read-replica MCP.

receipts@<subdomain> -> SES receipt rule -> S3 (raw/)          # the archive
Mac (receipts-email primary) -> S3 (replica/)                  # the replica
S3 (replica/) -> read-only MCP Lambda -> /email/mcp gateway    # the reader

AWS never parses mail. The one parser set lives in the receipts-email repo on
the Mac: ``emlrec pull-ses`` downloads new raw/ objects, parses and
reconciles them locally, and ``emlrec replicate`` publishes a ``VACUUM INTO``
snapshot of the SQLite primary under replica/. The Lambda serves that
snapshot; writes stay on the primary. (An earlier revision ran a second copy
of every parser in a Lambda that wrote parsed/ JSON nothing consumed — see
EMAIL_RECEIPT_INBOX.md for why it was removed.)

DNS (MX + DKIM CNAMEs) is created on an isolated subdomain so the root
domain's mail posture is untouched.

CAUTION: SES allows ONE active receipt rule set per account+region.
``activate=True`` claims it; safe on an account with no prior SES receiving,
but review before enabling anywhere SES receiving already exists. The ATS
verification inbox adds its rule to this set rather than creating another.
"""

from __future__ import annotations

import os
from typing import Optional

import pulumi
import pulumi_aws as aws
from pulumi import ComponentResource, ResourceOptions

LAMBDA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "lambdas"
)
REPLICA_PREFIX = "replica/"
REPLICA_DB_KEY = f"{REPLICA_PREFIX}email_receipts.db.gz"
REPLICA_MANIFEST_KEY = f"{REPLICA_PREFIX}manifest.json"


class EmailReceiptInbox(ComponentResource):
    """SES -> S3 raw/ archive, S3 replica/ snapshot, read-only MCP Lambda."""

    def __init__(
        self,
        name: str,
        zone_name: str = "tylernorlund.com",
        subdomain: str = "in",
        recipient_localpart: str = "receipts",
        activate: bool = True,
        raw_retention_days: Optional[int] = None,
        allowed_origins: str = (
            "https://claude.ai,https://claude.com,"
            "https://www.cursor.com,https://cursor.com"
        ),
        tags: Optional[dict[str, str]] = None,
        opts: Optional[ResourceOptions] = None,
    ):
        super().__init__("portfolio:infra:EmailReceiptInbox", name, None, opts)
        stack = pulumi.get_stack()
        child = ResourceOptions(parent=self)
        region = aws.get_region().region
        account_id = aws.get_caller_identity().account_id
        tags = {
            "Environment": stack,
            "Component": "email-receipt-inbox",
            **(tags or {}),
        }

        domain = f"{subdomain}.{zone_name}"
        self.domain = domain
        self.address = f"{recipient_localpart}@{domain}"
        zone = aws.route53.get_zone(name=zone_name)

        # Deterministic SES physical names so the bucket policy can pin the
        # exact receipt rule allowed to write under raw/ (confused-deputy guard).
        rule_set_name = f"{name}-{stack}"
        store_rule_name = f"{name}-store-{stack}"
        store_rule_arn = (
            f"arn:aws:ses:{region}:{account_id}:"
            f"receipt-rule-set/{rule_set_name}:"
            f"receipt-rule/{store_rule_name}"
        )

        # --- SES identity + DKIM + inbound MX on the isolated subdomain
        identity = aws.ses.DomainIdentity(
            f"{name}-identity", domain=domain, opts=child
        )
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
            opts=child,
        )
        # Block dependent resources until SES observes the record and marks the
        # identity verified.
        verification = aws.ses.DomainIdentityVerification(
            f"{name}-identity-verified",
            domain=identity.id,
            opts=ResourceOptions(
                parent=self, depends_on=[verification_record]
            ),
        )
        dkim = aws.ses.DomainDkim(
            f"{name}-dkim", domain=identity.domain, opts=child
        )
        for i in range(3):
            token = dkim.dkim_tokens[i]
            aws.route53.Record(
                f"{name}-dkim-{i}",
                zone_id=zone.zone_id,
                name=token.apply(lambda t: f"{t}._domainkey.{domain}"),
                type="CNAME",
                ttl=300,
                records=[token.apply(lambda t: f"{t}.dkim.amazonses.com")],
                opts=child,
            )
        # The inbound MX record is published LAST (see end of __init__): once it
        # exists, SES can accept mail, so everything a delivered message needs
        # (bucket versioning, an active rule set) must already be in place or
        # the first message is lost/unversioned.

        # --- mail bucket: raw/ (SES writes) + replica/ (the Mac writes)
        self.bucket = aws.s3.Bucket(
            f"{name}-mail",
            bucket=f"{name}-mail-{stack}-{account_id}",
            tags=tags,
            opts=child,
        )
        aws.s3.BucketPublicAccessBlock(
            f"{name}-mail-pab",
            bucket=self.bucket.id,
            block_public_acls=True,
            block_public_policy=True,
            ignore_public_acls=True,
            restrict_public_buckets=True,
            opts=child,
        )
        aws.s3.BucketServerSideEncryptionConfiguration(
            f"{name}-mail-sse",
            bucket=self.bucket.id,
            rules=[
                {
                    "apply_server_side_encryption_by_default": {
                        "sse_algorithm": "AES256"
                    }
                }
            ],
            opts=child,
        )
        # Versioning gives raw/ replays an immutable lineage and lets a bad
        # replica publish be rolled back to the previous snapshot.
        versioning = aws.s3.BucketVersioning(
            f"{name}-mail-versioning",
            bucket=self.bucket.id,
            versioning_configuration={"status": "Enabled"},
            opts=child,
        )
        lifecycle_rules = [
            {
                # Every replicate uploads a new version; keep a week of
                # rollbacks, not forever.
                "id": "expire-replica-versions",
                "status": "Enabled",
                "filter": {"prefix": REPLICA_PREFIX},
                "noncurrent_version_expiration": {"noncurrent_days": 7},
            }
        ]
        if raw_retention_days:
            lifecycle_rules.append(
                {
                    "id": "expire-raw",
                    "status": "Enabled",
                    "filter": {"prefix": "raw/"},
                    "expiration": {"days": raw_retention_days},
                    "noncurrent_version_expiration": {
                        "noncurrent_days": raw_retention_days
                    },
                }
            )
        aws.s3.BucketLifecycleConfiguration(
            f"{name}-mail-lifecycle",
            bucket=self.bucket.id,
            rules=lifecycle_rules,
            # Noncurrent-version expiration is meaningless until versioning
            # is Enabled; order it after so the rule isn't applied to an
            # unversioned bucket.
            opts=ResourceOptions(parent=self, depends_on=[versioning]),
        )
        bucket_policy = aws.s3.BucketPolicy(
            f"{name}-mail-ses-policy",
            bucket=self.bucket.id,
            policy=pulumi.Output.all(self.bucket.arn, account_id).apply(
                lambda a: pulumi.Output.json_dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Sid": "AllowSESPuts",
                                "Effect": "Allow",
                                "Principal": {"Service": "ses.amazonaws.com"},
                                "Action": "s3:PutObject",
                                "Resource": f"{a[0]}/raw/*",
                                # Scope to this account AND the specific receipt rule, so
                                # no other SES rule in the account can write under raw/.
                                "Condition": {
                                    "StringEquals": {
                                        "aws:SourceAccount": a[1],
                                        "aws:SourceArn": store_rule_arn,
                                    }
                                },
                            }
                        ],
                    }
                )
            ),
            opts=child,
        )

        # --- receipt rule set
        rule_set = aws.ses.ReceiptRuleSet(
            f"{name}-rules", rule_set_name=rule_set_name, opts=child
        )
        self.rule_set = rule_set
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
            s3_actions=[
                {
                    "bucket_name": self.bucket.bucket,
                    "object_key_prefix": "raw/",
                    "position": 1,
                }
            ],
            # SES validates at create time that (a) the identity is verified and
            # (b) it can write to the bucket, so both must precede the rule.
            opts=ResourceOptions(
                parent=self, depends_on=[rule_set, verification, bucket_policy]
            ),
        )
        activation = None
        if activate:
            # Activate only after the rule exists (never briefly publish an
            # empty active set) AND after versioning is on, so the first
            # stored message is versioned.
            activation = aws.ses.ActiveReceiptRuleSet(
                f"{name}-rules-active",
                rule_set_name=rule_set.rule_set_name,
                opts=ResourceOptions(
                    parent=self,
                    depends_on=[store_rule, versioning],
                ),
            )
        self.activation = activation

        # --- inbound MX, published LAST. Once this resolves SES starts
        # accepting mail; gating it on the active rule set plus versioning
        # means no delivered message can arrive before the storage exists.
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
                    depends_on=[store_rule, versioning, activation],
                ),
            )

        # --- read-replica MCP Lambda (stdlib only: boto3 + sqlite3)
        mcp_role = aws.iam.Role(
            f"{name}-mcp-role",
            assume_role_policy=pulumi.Output.json_dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Action": "sts:AssumeRole",
                            "Effect": "Allow",
                            "Principal": {"Service": "lambda.amazonaws.com"},
                        }
                    ],
                }
            ),
            tags=tags,
            opts=child,
        )
        mcp_logs = aws.iam.RolePolicyAttachment(
            f"{name}-mcp-logs",
            role=mcp_role.name,
            policy_arn=aws.iam.ManagedPolicy.AWS_LAMBDA_BASIC_EXECUTION_ROLE,
            opts=child,
        )
        # Read the replica prefix and nothing else: raw mail is never
        # reachable from the MCP, even via query_sql.
        mcp_policy = aws.iam.RolePolicy(
            f"{name}-mcp-replica-read",
            role=mcp_role.id,
            policy=self.bucket.arn.apply(
                lambda bucket_arn: pulumi.Output.json_dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "s3:GetObject",
                                    "s3:GetObjectVersion",
                                ],
                                "Resource": f"{bucket_arn}/{REPLICA_PREFIX}*",
                            },
                            {
                                "Effect": "Allow",
                                "Action": "s3:ListBucket",
                                "Resource": bucket_arn,
                                "Condition": {
                                    "StringLike": {
                                        "s3:prefix": [f"{REPLICA_PREFIX}*"]
                                    }
                                },
                            },
                        ],
                    }
                )
            ),
            opts=child,
        )
        self.mcp_lambda = aws.lambda_.Function(
            f"{name}-mcp",
            runtime="python3.13",
            handler="mcp.lambda_handler",
            role=mcp_role.arn,
            # The gateway integration window is 29s; leave headroom so a
            # cold start (download + gunzip ~5 MB) still answers in time.
            timeout=25,
            memory_size=512,
            reserved_concurrent_executions=5,
            code=pulumi.AssetArchive(
                {
                    "mcp.py": pulumi.FileAsset(
                        os.path.join(LAMBDA_DIR, "mcp.py")
                    ),
                    "queries.py": pulumi.FileAsset(
                        os.path.join(LAMBDA_DIR, "queries.py")
                    ),
                }
            ),
            environment={
                "variables": {
                    "REPLICA_BUCKET": self.bucket.bucket,
                    "REPLICA_DB_KEY": REPLICA_DB_KEY,
                    "REPLICA_MANIFEST_KEY": REPLICA_MANIFEST_KEY,
                    "ALLOWED_ORIGINS": allowed_origins,
                }
            },
            tags=tags,
            opts=ResourceOptions(
                parent=self, depends_on=[mcp_policy, mcp_logs]
            ),
        )
        self.replica_db_key = REPLICA_DB_KEY
        self.replica_manifest_key = REPLICA_MANIFEST_KEY

        self.register_outputs(
            {
                "address": self.address,
                "domain": self.domain,
                "rule_set_name": self.rule_set.rule_set_name,
                "bucket": self.bucket.bucket,
                "replica_db_key": REPLICA_DB_KEY,
                "mcp_lambda_arn": self.mcp_lambda.arn,
            }
        )
