# FINAL VERDICT review — an automated fix loop completed 6 rounds on this PR.
You are the same reviewer. Below: current diff, core files, the full decision log, and your last review.
Judge ONLY the CURRENT state. If no [high] or [med] findings remain, respond with exactly: APPROVED-NO-SUGGESTIONS
([low]/[deadlock] items already in the human ledger do not block approval; do not re-list them.)
Otherwise list remaining [high]/[med] findings only, numbered, with file and fix.

## Diff vs origin/main
```diff
diff --git a/infra/__main__.py b/infra/__main__.py
index 9563c770e..e6be0de2a 100644
--- a/infra/__main__.py
+++ b/infra/__main__.py
@@ -1594,3 +1594,16 @@ if hasattr(api_gateway, "api"):
         lambda_function=qa_viz_cache.api_lambda,
         permission_name="qa_viz_lambda_permission",
     )
+
+
+# Inbound email receipt pipeline (SES -> S3 -> parser Lambda -> S3 parsed/).
+# Gated off by default: enable per-stack with
+#   pulumi config set portfolio:email_receipt_inbox_enabled true
+# CAUTION: activates the account's SES receipt rule set (one active per
+# account+region) — see email_receipt_inbox/infrastructure.py.
+if portfolio_config.get_bool("email_receipt_inbox_enabled"):
+    from email_receipt_inbox import EmailReceiptInbox
+
+    email_inbox = EmailReceiptInbox("email-receipt-inbox")
+    pulumi.export("email_receipt_inbox_address", email_inbox.address)
+    pulumi.export("email_receipt_inbox_bucket", email_inbox.bucket.bucket)
diff --git a/infra/email_receipt_inbox/__init__.py b/infra/email_receipt_inbox/__init__.py
new file mode 100644
index 000000000..439a95e1c
--- /dev/null
+++ b/infra/email_receipt_inbox/__init__.py
@@ -0,0 +1,4 @@
+"""Inbound email receipt pipeline component."""
+from email_receipt_inbox.infrastructure import EmailReceiptInbox
+
+__all__ = ["EmailReceiptInbox"]
diff --git a/infra/email_receipt_inbox/infrastructure.py b/infra/email_receipt_inbox/infrastructure.py
new file mode 100644
index 000000000..f4d2228d6
--- /dev/null
+++ b/infra/email_receipt_inbox/infrastructure.py
@@ -0,0 +1,326 @@
+"""SES inbound email pipeline for receipt ingestion.
+
+receipts@<subdomain> -> SES receipt rule -> S3 (raw/) -> Lambda parser
+-> S3 (parsed/ JSON). The private reconciliation plane consumes parsed/.
+
+DNS (MX + DKIM CNAMEs) is created on an isolated subdomain so the root
+domain's mail posture is untouched.
+
+CAUTION: SES allows ONE active receipt rule set per account+region.
+``activate=True`` claims it; safe on an account with no prior SES receiving,
+but review before enabling anywhere SES receiving already exists.
+"""
+from __future__ import annotations
+
+import os
+from typing import Optional
+
+import pulumi
+import pulumi_aws as aws
+from pulumi import ComponentResource, ResourceOptions
+
+LAMBDA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lambdas")
+
+
+class EmailReceiptInbox(ComponentResource):
+    """Inbound receipt-email pipeline: SES -> S3 -> parser Lambda -> S3."""
+
+    def __init__(
+        self,
+        name: str,
+        zone_name: str = "tylernorlund.com",
+        subdomain: str = "in",
+        recipient_localpart: str = "receipts",
+        activate: bool = True,
+        raw_retention_days: Optional[int] = None,
+        tags: Optional[dict[str, str]] = None,
+        opts: Optional[ResourceOptions] = None,
+    ):
+        super().__init__("portfolio:infra:EmailReceiptInbox", name, None, opts)
+        stack = pulumi.get_stack()
+        child = ResourceOptions(parent=self)
+        region = aws.get_region().region
+        account_id = aws.get_caller_identity().account_id
+        tags = {"Environment": stack, "Component": "email-receipt-inbox",
+                **(tags or {})}
+
+        domain = f"{subdomain}.{zone_name}"
+        self.address = f"{recipient_localpart}@{domain}"
+        zone = aws.route53.get_zone(name=zone_name)
+
+        # Deterministic SES physical names so the bucket policy can pin the
+        # exact receipt rule allowed to write under raw/ (confused-deputy guard).
+        rule_set_name = f"{name}-{stack}"
+        store_rule_name = f"{name}-store-{stack}"
+        store_rule_arn = (f"arn:aws:ses:{region}:{account_id}:"
+                          f"receipt-rule-set/{rule_set_name}:"
+                          f"receipt-rule/{store_rule_name}")
+
+        # --- SES identity + DKIM + inbound MX on the isolated subdomain
+        identity = aws.ses.DomainIdentity(f"{name}-identity", domain=domain,
+                                          opts=child)
+        # Publish the domain-verification TXT so SES can actually verify the
+        # identity (unverified identities silently reject inbound mail). Record
+        # lives under the isolated ``in.`` subdomain — the root zone is untouched.
+        verification_record = aws.route53.Record(
+            f"{name}-verify",
+            zone_id=zone.zone_id,
+            name=f"_amazonses.{domain}",
+            type="TXT",
+            ttl=600,
+            records=[identity.verification_token],
+            opts=child)
+        # Block dependent resources until SES observes the record and marks the
+        # identity verified.
+        verification = aws.ses.DomainIdentityVerification(
+            f"{name}-identity-verified",
+            domain=identity.id,
+            opts=ResourceOptions(parent=self, depends_on=[verification_record]))
+        dkim = aws.ses.DomainDkim(f"{name}-dkim", domain=identity.domain,
+                                  opts=child)
+        for i in range(3):
+            token = dkim.dkim_tokens[i]
+            aws.route53.Record(
+                f"{name}-dkim-{i}",
+                zone_id=zone.zone_id,
+                name=token.apply(lambda t: f"{t}._domainkey.{domain}"),
+                type="CNAME",
+                ttl=300,
+                records=[token.apply(lambda t: f"{t}.dkim.amazonses.com")],
+                opts=child)
+        # The inbound MX record is published LAST (see end of __init__): once it
+        # exists, SES can accept mail, so everything a delivered message needs
+        # (bucket versioning, the S3->Lambda notification, an active rule set)
+        # must already be in place or the first message is lost/unversioned.
+
+        # --- raw + parsed mail bucket
+        self.bucket = aws.s3.Bucket(
+            f"{name}-mail",
+            bucket=f"{name}-mail-{stack}-{account_id}",
+            tags=tags,
+            opts=child)
+        aws.s3.BucketPublicAccessBlock(
+            f"{name}-mail-pab",
+            bucket=self.bucket.id,
+            block_public_acls=True, block_public_policy=True,
+            ignore_public_acls=True, restrict_public_buckets=True,
+            opts=child)
+        aws.s3.BucketServerSideEncryptionConfiguration(
+            f"{name}-mail-sse",
+            bucket=self.bucket.id,
+            rules=[{"apply_server_side_encryption_by_default": {
+                "sse_algorithm": "AES256"}}],
+            opts=child)
+        # Versioning gives replays/overwrites an immutable lineage: the handler
+        # fetches the exact event version rather than "latest" (see handler.py).
+        versioning = aws.s3.BucketVersioning(
+            f"{name}-mail-versioning",
+            bucket=self.bucket.id,
+            versioning_configuration={"status": "Enabled"},
+            opts=child)
+        if raw_retention_days:
+            # Expire both prefixes on the same clock; with versioning enabled,
+            # also expire noncurrent versions so replays don't accumulate.
+            expire = {"days": raw_retention_days}
+            noncurrent = {"noncurrent_days": raw_retention_days}
+            aws.s3.BucketLifecycleConfiguration(
+                f"{name}-mail-lifecycle",
+                bucket=self.bucket.id,
+                rules=[
+                    {"id": "expire-raw", "status": "Enabled",
+                     "filter": {"prefix": "raw/"},
+                     "expiration": expire,
+                     "noncurrent_version_expiration": noncurrent},
+                    {"id": "expire-parsed", "status": "Enabled",
+                     "filter": {"prefix": "parsed/"},
+                     "expiration": expire,
+                     "noncurrent_version_expiration": noncurrent},
+                ],
+                # Noncurrent-version expiration is meaningless until versioning
+                # is Enabled; order it after so the rule isn't applied to an
+                # unversioned bucket.
+                opts=ResourceOptions(parent=self, depends_on=[versioning]))
+        bucket_policy = aws.s3.BucketPolicy(
+            f"{name}-mail-ses-policy",
+            bucket=self.bucket.id,
+            policy=pulumi.Output.all(self.bucket.arn, account_id).apply(
+                lambda a: pulumi.Output.json_dumps({
+                    "Version": "2012-10-17",
+                    "Statement": [{
+                        "Sid": "AllowSESPuts",
+                        "Effect": "Allow",
+                        "Principal": {"Service": "ses.amazonaws.com"},
+                        "Action": "s3:PutObject",
+                        "Resource": f"{a[0]}/raw/*",
+                        # Scope to this account AND the specific receipt rule, so
+                        # no other SES rule in the account can write under raw/.
+                        "Condition": {"StringEquals": {
+                            "aws:SourceAccount": a[1],
+                            "aws:SourceArn": store_rule_arn}},
+                    }],
+                })),
+            opts=child)
+
+        # --- parser Lambda
+        role = aws.iam.Role(
+            f"{name}-parser-role",
+            assume_role_policy=pulumi.Output.json_dumps({
+                "Version": "2012-10-17",
+                "Statement": [{"Action": "sts:AssumeRole",
+                               "Effect": "Allow",
+                               "Principal": {"Service": "lambda.amazonaws.com"}}],
+            }),
+            tags=tags, opts=child)
+        logs_attach = aws.iam.RolePolicyAttachment(
+            f"{name}-parser-logs",
+            role=role.name,
+            policy_arn=aws.iam.ManagedPolicy.AWS_LAMBDA_BASIC_EXECUTION_ROLE,
+            opts=child)
+        # --- durable backstop: async S3 invokes that exhaust Lambda retries
+        # land in a DLQ instead of being silently discarded (see FunctionEvent
+        # InvokeConfig below).
+        dlq = aws.sqs.Queue(
+            f"{name}-parser-dlq",
+            message_retention_seconds=1209600,  # 14 days
+            tags=tags, opts=child)
+        s3_policy = aws.iam.RolePolicy(
+            f"{name}-parser-s3",
+            role=role.id,
+            policy=pulumi.Output.all(self.bucket.arn, dlq.arn).apply(
+                lambda a: pulumi.Output.json_dumps({
+                    "Version": "2012-10-17",
+                    "Statement": [
+                        {"Effect": "Allow",
+                         "Action": ["s3:GetObject", "s3:GetObjectVersion"],
+                         "Resource": f"{a[0]}/raw/*"},
+                        {"Effect": "Allow", "Action": ["s3:PutObject"],
+                         "Resource": f"{a[0]}/parsed/*"},
+                        {"Effect": "Allow", "Action": ["sqs:SendMessage"],
+                         "Resource": a[1]},
+                    ],
+                })),
+            opts=child)
+        self.parser = aws.lambda_.Function(
+            f"{name}-parser",
+            runtime="python3.12",
+            handler="handler.lambda_handler",
+            role=role.arn,
+            timeout=60,
+            memory_size=256,
+            # Public ingress: cap this function's slice of the account's shared
+            # concurrency so a mail flood cannot starve every other Lambda.
+            # Throttled async S3 invokes retry, so the cap loses no mail.
+            reserved_concurrent_executions=10,
+            code=pulumi.AssetArchive({
+                ".": pulumi.FileArchive(LAMBDA_DIR),
+            }),
+            tags=tags,
+            # The function must not exist (and thus be invokable) before its
+            # execution-role policies are attached, or early invokes fail
+            # AccessDenied on GetObject/PutObject.
+            opts=ResourceOptions(parent=self,
+                                 depends_on=[s3_policy, logs_attach]))
+        # Route async invokes that exhaust retries to the DLQ. Bound the retry
+        # window so a persistently failing event drains to the DLQ within the
+        # hour instead of retrying against the 6h default.
+        async_config = aws.lambda_.FunctionEventInvokeConfig(
+            f"{name}-parser-async",
+            function_name=self.parser.name,
+            maximum_retry_attempts=2,
+            maximum_event_age_in_seconds=3600,
+            destination_config={"on_failure": {"destination": dlq.arn}},
+            opts=child)
+        invoke_perm = aws.lambda_.Permission(
+            f"{name}-parser-s3-invoke",
+            action="lambda:InvokeFunction",
+            function=self.parser.name,
+            principal="s3.amazonaws.com",
+            source_arn=self.bucket.arn,
+            opts=child)
+        notification = aws.s3.BucketNotification(
+            f"{name}-mail-notify",
+            bucket=self.bucket.id,
+            lambda_functions=[{
+                "lambda_function_arn": self.parser.arn,
+                "events": ["s3:ObjectCreated:*"],
+                "filter_prefix": "raw/",
+            }],
+            # S3 validates it can invoke the target at create time, so the
+            # invoke permission must already exist. Also gate on the async
+            # config so the DLQ failure destination is in place before any
+            # delivered message can invoke the parser — otherwise an early
+            # failure exhausts the default retries and is silently discarded.
+            # activation and the MX record both depend on this notification, so
+            # this dependency propagates to the whole mail-accepting graph.
+            opts=ResourceOptions(
+                parent=self,
+                depends_on=[self.parser, invoke_perm, async_config]))
+
+        # --- receipt rule set
+        rule_set = aws.ses.ReceiptRuleSet(
+            f"{name}-rules", rule_set_name=rule_set_name, opts=child)
+        store_rule = aws.ses.ReceiptRule(
+            f"{name}-store-rule",
+            name=store_rule_name,
+            rule_set_name=rule_set.rule_set_name,
+            recipients=[self.address],
+            enabled=True,
+            scan_enabled=True,
+            # Financial mail: reject plaintext delivery rather than default to
+            # opportunistic (Optional) TLS.
+            tls_policy="Require",
+            s3_actions=[{
+                "bucket_name": self.bucket.bucket,
+                "object_key_prefix": "raw/",
+                "position": 1,
+            }],
+            # SES validates at create time that (a) the identity is verified and
+            # (b) it can write to the bucket, so both must precede the rule.
+            opts=ResourceOptions(
+                parent=self,
+                depends_on=[rule_set, verification, bucket_policy]))
+        activation = None
+        if activate:
+            # Activate only after the rule exists (never briefly publish an
+            # empty active set) AND after the bucket pipeline is ready, so the
+            # first stored message is versioned and triggers the parser.
+            activation = aws.ses.ActiveReceiptRuleSet(
+                f"{name}-rules-active",
+                rule_set_name=rule_set.rule_set_name,
+                opts=ResourceOptions(
+                    parent=self,
+                    depends_on=[store_rule, versioning, notification]))
+
+        # --- inbound MX, published LAST. Once this resolves SES starts
+        # accepting mail; gating it on the active rule set plus versioning +
+        # notification means no delivered message can arrive before the
+        # storage/trigger graph exists.
+        #
+        # Only publish MX once this rule set is actually the one SES will
+        # evaluate. SES has a single active receipt rule set per account+region,
+        # and this component's store rule lives ONLY inside the rule set it
+        # creates here — it is never merged into a foreign set. So the sole way
+        # this recipient's mail gets stored is if we activate our own set. If we
+        # did not (``activate=False``), publishing MX would route mail to
+        # whatever OTHER set is active — which has no rule for this recipient —
+        # so the message is rejected or dropped instead of stored. Therefore MX
+        # is published only when we activated our set (``activation is not
+        # None``), with the MX record depending on that activation.
+        if activation is not None:
+            aws.route53.Record(
+                f"{name}-mx",
+                zone_id=zone.zone_id,
+                name=domain,
+                type="MX",
+                ttl=300,
+                records=[f"10 inbound-smtp.{region}.amazonaws.com"],
+                opts=ResourceOptions(
+                    parent=self,
+                    depends_on=[store_rule, versioning, notification,
+                                activation]))
+
+        self.register_outputs({
+            "address": self.address,
+            "bucket": self.bucket.bucket,
+            "parser_arn": self.parser.arn,
+        })
diff --git a/infra/email_receipt_inbox/lambdas/handler.py b/infra/email_receipt_inbox/lambdas/handler.py
new file mode 100644
index 000000000..f280a7c55
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/handler.py
@@ -0,0 +1,233 @@
+"""S3-triggered parser for inbound receipt emails.
+
+SES writes each inbound message to s3://<bucket>/raw/<messageId>. This handler
+parses it through the sender registry and writes a result document to
+parsed/<messageId>.<ingest_id>.json:
+
+    {
+      "message_id": "...",        # RFC-822 Message-ID (falls back to S3 key)
+      "ingest_id": "...",         # sha256 of the raw bytes — the stable, non-
+                                  # spoofable identity keying the parsed object
+      "s3_key": "raw/...",
+      "from_domain": "doordash.com",
+      "original_from": "...",     # unwrapped when the mail arrived via a
+                                  # forwarding rule (X-Forwarded-For et al.)
+      "subject": "...",
+      "group": "doordash" | null,
+      "classification": "receipt" | "txn_signal" | "needs_ocr" | "non_receipt"
+                        | "quarantine" | "unknown_sender" | "parse_error",
+      "receipt": {...} | null,    # the parser's raw schema output
+      "error": "..." | null
+    }
+
+The private reconciliation plane polls parsed/ and MUST ingest idempotently by
+``ingest_id`` (the content digest): S3 notifications are unordered, so keying by
+message_id/S3 key alone would let an older replay clobber a newer parse. Each
+distinct message body maps to exactly one parsed object. This function
+deliberately writes derived data only — no DynamoDB coupling — so re-parses are
+a matter of re-running over raw/.
+"""
+from __future__ import annotations
+
+import email
+import email.errors
+import email.policy
+import email.utils
+import hashlib
+import json
+import os
+import re
+import tempfile
+import traceback
+import urllib.parse
+
+import boto3
+
+import registry
+
+s3 = boto3.client("s3")
+
+# SES delivers S3-stored messages up to 40 MB, but receipt emails are a few
+# hundred KB. This 256 MB function holds the raw bytes, the parsed MIME tree, a
+# normalized copy, and parser-specific representations at once, so a large
+# payload can OOM. Reject anything oversized BEFORE fetching it into memory.
+MAX_RAW_BYTES = 15 * 1024 * 1024
+
+
+def _from_domain(msg) -> tuple[str, str]:
+    """Return (from_addr, domain) for sender dispatch.
+
+    ``Reply-To`` is deliberately NOT consulted: it is free-form and often
+    points marketing/spoofed mail at an unrelated domain. ``X-Original-From``
+    is honored only for the documented iCloud auto-forwarding case (the
+    forwarder rewrites ``From`` to itself but preserves the original sender
+    here); the SES authentication verdicts recorded alongside let the
+    downstream reconciliation plane decide how much to trust it.
+    """
+    for header in ("X-Original-From", "From"):
+        raw = msg.get(header)
+        if not raw:
+            continue
+        addr = email.utils.parseaddr(raw)[1]
+        m = re.search(r"@([\w.\-]+)", addr or "")
+        if m:
+            return addr, m.group(1).lower()
+    return "", ""
+
+
+def _ses_auth(msg) -> dict:
+    """Extract the SES-stamped spam/virus verdicts and SPF/DKIM/DMARC results.
+
+    SES writes these headers into the stored message; scan_enabled only
+    *produces* them, it never rejects, so the pipeline must gate on them.
+    """
+    spam = (msg.get("X-SES-Spam-Verdict") or "").strip().upper() or None
+    virus = (msg.get("X-SES-Virus-Verdict") or "").strip().upper() or None
+    ar = " ".join(msg.get_all("Authentication-Results") or [])
+    auth = {}
+    for mech in ("spf", "dkim", "dmarc"):
+        m = re.search(rf"\b{mech}=(\w+)", ar)
+        if m:
+            auth[mech] = m.group(1).lower()
+    return {"spam_verdict": spam, "virus_verdict": virus, "auth": auth}
+
+
+def _content_rejected(spam, virus) -> bool:
+    """Fail CLOSED on indeterminate SES content scans.
+
+    SES emits PASS / FAIL / GRAY / PROCESSING_FAILED, and a verdict may be
+    absent entirely if the message could not be scanned. Only an explicit
+    ``PASS`` clears the virus scan — GRAY, PROCESSING_FAILED, FAIL, or a missing
+    verdict all mean SES never affirmed the payload is clean, so quarantine.
+    For spam, an explicit FAIL is spam and PROCESSING_FAILED means SES could not
+    scan (e.g. malformed MIME); GRAY (borderline) is allowed through with the
+    verdict recorded so downstream can weigh it.
+    """
+    if virus != "PASS":
+        return True
+    if spam in ("FAIL", "PROCESSING_FAILED"):
+        return True
+    return False
+
+
+def lambda_handler(event, _context):
+    results = []
+    for record in event.get("Records", []):
+        obj = record["s3"]["object"]
+        bucket = record["s3"]["bucket"]["name"]
+        key = urllib.parse.unquote_plus(obj["key"])
+        version_id = obj.get("versionId")
+        out = {"s3_key": key, "version_id": version_id,
+               "etag": obj.get("eTag"), "sequencer": obj.get("sequencer"),
+               "group": None, "classification": "unknown_sender",
+               "receipt": None, "error": None}
+
+        # Size guard BEFORE any fetch: an oversized payload is quarantined
+        # without ever loading its body into memory. Identity falls back to the
+        # S3 ETag since we deliberately never hash the (unread) bytes; the
+        # object is still recorded under parsed/ rather than silently dropped.
+        size = obj.get("size")
+        if size is not None and size > MAX_RAW_BYTES:
+            ident = (obj.get("eTag") or "oversized").strip('"')
+            out["ingest_id"] = ident
+            out["classification"] = "quarantine"
+            out["error"] = f"oversized: {size} bytes exceeds {MAX_RAW_BYTES}"
+            dest = ("parsed/" + key.split("/", 1)[-1] + "." + ident + ".json")
+            s3.put_object(
+                Bucket=bucket, Key=dest,
+                Body=json.dumps(out, ensure_ascii=False, default=str).encode(),
+                ContentType="application/json")
+            results.append({"key": key,
+                            "classification": out["classification"]})
+            continue
+
+        # Infra fetch stays OUTSIDE the parse-error catch: throttling, transient
+        # GetObject failures, and missing-object races must raise so Lambda
+        # retries them (and, exhausted, land in the DLQ) rather than being
+        # persisted as a permanent parse_error + reported as success.
+        get_kwargs = {"Bucket": bucket, "Key": key}
+        if version_id:
+            get_kwargs["VersionId"] = version_id
+        raw = s3.get_object(**get_kwargs)["Body"].read()
+        # Immutable identity: content digest, independent of the sender's
+        # spoofable RFC Message-ID (which is retained separately below).
+        out["ingest_id"] = hashlib.sha256(raw).hexdigest()
+
+        try:
+            msg = email.message_from_bytes(raw, policy=email.policy.default)
+            out["message_id"] = (msg.get("Message-ID") or key).strip()
+            out["subject"] = msg.get("Subject", "")
+            out.update(_ses_auth(msg))
+            from_addr, domain = _from_domain(msg)
+            out["from_domain"] = domain
+            out["original_from"] = from_addr
+            if _content_rejected(spam=out["spam_verdict"],
+                                 virus=out["virus_verdict"]):
+                # SES flagged (or could not clear) malware/spam; hold it out of
+                # reconciliation. Distinct from parser-determined non_receipt so
+                # downstream can treat scan failures differently.
+                out["classification"] = "quarantine"
+            else:
+                grp = registry.group_for_domain(domain)
+                out["group"] = grp
+                if grp:
+                    # The registry chose the group from the canonical sender
+                    # (X-Original-From in the iCloud-forwarding case), but the
+                    # parsers re-dispatch on the message's own ``From`` header —
+                    # which the forwarder rewrote to itself. Normalize ONLY the
+                    # parser's temp copy so its From matches the sender the
+                    # registry keyed on; the stored raw/ evidence is untouched.
+                    parser_bytes = raw
+                    orig_from = msg.get("X-Original-From")
+                    if orig_from:
+                        norm = email.message_from_bytes(
+                            raw, policy=email.policy.default)
+                        if norm.get("From") is not None:
+                            norm.replace_header("From", orig_from)
+                        else:
+                            norm["From"] = orig_from
+                        parser_bytes = norm.as_bytes()
+                    with tempfile.NamedTemporaryFile(
+                            suffix=".eml", delete=False) as tmp:
+                        tmp.write(parser_bytes)
+                        path = tmp.name
+                    try:
+                        parsed = registry.run_parser(grp, path)
+                        if isinstance(parsed, list):
+                            parsed = parsed[0] if parsed else {}
+                        out["classification"] = registry.classify(
+                            grp, out["subject"], parsed)
+                        if out["classification"] in ("receipt", "txn_signal"):
+                            out["receipt"] = parsed
+                    finally:
+                        os.unlink(path)
+        except (ValueError, KeyError, IndexError, AttributeError, TypeError,
+                email.errors.MessageError):
+            # Deterministic email/parser validation failures on THIS message
+            # body are permanent: recording parse_error and moving on is
+            # correct — a retry would fail identically. Integration defects are
+            # NOT reachable here: the parser entry points are resolved and
+            # signature-checked at cold start and their return type is validated
+            # in run_parser, so a missing/renamed entry point, a bad signature,
+            # or a wrong return type raises registry.ParserContractError (not a
+            # subclass of the exceptions above) and propagates. Operational
+            # failures — ImportError (broken deploy), OSError (temp-file/disk),
+            # and anything else unexpected — likewise propagate so Lambda
+            # retries them and, once exhausted, routes the event to the DLQ
+            # instead of persisting a false parse_error and reporting success.
+            out["classification"] = "parse_error"
+            out["error"] = traceback.format_exc(limit=4)
+
+        # put_object is likewise retryable — keep it outside the catch.
+        # Key by content digest, not the raw key: two versions of the same S3
+        # key (a replay of the same messageId) would otherwise collide on one
+        # parsed object, and unordered notifications could let the older version
+        # win. ingest_id makes each distinct body its own idempotent object.
+        dest = ("parsed/" + key.split("/", 1)[-1] + "."
+                + out["ingest_id"] + ".json")
+        s3.put_object(
+            Bucket=bucket, Key=dest,
+            Body=json.dumps(out, ensure_ascii=False, default=str).encode(),
+            ContentType="application/json")
+        results.append({"key": key, "classification": out["classification"]})
+    return {"processed": results}
diff --git a/infra/email_receipt_inbox/lambdas/registry.py b/infra/email_receipt_inbox/lambdas/registry.py
new file mode 100644
index 000000000..8a9cff8de
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/registry.py
@@ -0,0 +1,174 @@
+"""Sender-domain -> parser registry for inbound email receipts.
+
+Parsers are stdlib-only modules exposing ``parse(path) -> dict`` (or
+``parse_eml``) that take an RFC-822 .eml file path and return the shared
+receipt schema (dollars as floats, ``grand_total is None`` for non-receipts).
+"""
+from __future__ import annotations
+
+import importlib
+import inspect
+import re
+from typing import Any, Optional
+
+
+class ParserContractError(Exception):
+    """A parser *integration* / return-contract violation.
+
+    Distinct from a per-message parse failure. Raised for systematic defects —
+    a missing or non-callable entry point, a signature that cannot accept the
+    single .eml path argument, or a return value that is not the shared receipt
+    schema (dict / list-of-dicts / None). These must PROPAGATE so Lambda
+    retries and, once exhausted, routes the event to the DLQ, rather than being
+    caught per-message and persisted as a false ``parse_error`` reported as
+    success. Deliberately not a subclass of ValueError/TypeError so the
+    handler's per-message parse catch never swallows it.
+    """
+
+# group -> (domain suffixes, module name, entry attr)
+GROUPS: dict[str, tuple[tuple[str, ...], str, str]] = {
+    "apple": (("email.apple.com", "applepay.apple.com", "orders.apple.com", "apple.com"),
+              "parse_apple", "parse"),
+    "doordash": (("doordash.com",), "parse_doordash", "parse_eml"),
+    "amazon": (("amazon.com",), "parse_amazon", "parse"),
+    "venmo": (("venmo.com",), "parse_venmo", "parse_eml"),
+    "paypal": (("paypal.com",), "parse_paypal", "parse_eml"),
+    "pos-restaurants": (("toasttab.com", "squareup.com", "square.com", "clover.com",
+                         "spoton.com"), "parse_pos_restaurants", "parse_eml"),
+    "uber": (("uber.com",), "parse_uber", "parse_eml"),
+    "retail": (("target.com", "bestbuy.com", "ebay.com", "starbucks.com"),
+               "parse_retail", "parse"),
+    "equinox": (("equinox.com",), "parse_equinox", "parse"),
+    "github": (("github.com",), "parse_github", "parse"),
+    "restaurant-platforms": (("chownow.com", "dylish.com", "oftendining.com"),
+                             "parse_restaurant_platforms", "parse"),
+    "sce": (("scewebservices.com",), "parse_restaurant_platforms", "parse"),
+    "services": (("socalgas.com", "digitalocean.com", "stripe.com",
+                  "accounts.nintendo.com"), "parse_services", "parse"),
+    "chase-alerts": (("chase.com",), "parse_chase_alerts", "parse"),
+    "costco": (("costco.com", "costco.com.mx"), "parse_costco", "parse"),
+    "travel-housing": (("airbnb.com", "tesla.com", "hellolanding.com",
+                        "landing.com"), "parse_travel_housing", "parse"),
+}
+
+# Notification/marketing templates whose bodies carry amounts that are NOT
+# purchase receipts (kept in sync with the private reconciliation plane).
+SUBJECT_DROP = {
+    "paypal": re.compile(r"transfer request|requested a hold|hold on the funds|"
+                         r"has been (removed|released)|policy|survey", re.I),
+    "retail": re.compile(r"is ending soon|relisted item|sent a message|watched item|"
+                         r"back in stock|price drop|\bbid\b|offer (received|declined|accepted)|"
+                         r"pick up where you left|invite|coupon|% off|\bsale\b|\bdeals?\b", re.I),
+    "sce": re.compile(r"bill is ready", re.I),
+    "services": re.compile(r"bill is ready", re.I),
+}
+
+
+def group_for_domain(domain: Optional[str]) -> Optional[str]:
+    d = (domain or "").lower()
+    for grp, (suffixes, _, _) in GROUPS.items():
+        for s in suffixes:
+            if d == s or d.endswith("." + s):
+                return grp
+    return None
+
+
+def _resolve_entrypoints() -> dict[str, Any]:
+    """Import every parser module and bind its callable entry point ONCE, at
+    cold start.
+
+    A broken deploy — a missing parser module (ImportError), a missing/renamed
+    entry attribute, or an entry that cannot be called with a single .eml path
+    — fails HERE, at import time, so the whole invocation errors and the event
+    retries / lands in the DLQ. Previously these surfaced only when a message
+    happened to route to the broken group, inside the handler's per-message
+    catch, where they were masked as a permanent ``parse_error`` and reported
+    as success. Resolving up front turns a silent, per-message data-loss bug
+    into a loud deploy failure.
+    """
+    resolved: dict[str, Any] = {}
+    for grp, (_, module_name, entry) in GROUPS.items():
+        mod = importlib.import_module(f"parsers.{module_name}")
+        fn = getattr(mod, entry, None)
+        if not callable(fn):
+            raise ParserContractError(
+                f"{grp}: parsers.{module_name}.{entry} is missing or not callable")
+        try:
+            inspect.signature(fn).bind("<eml-path>")
+        except TypeError as exc:
+            raise ParserContractError(
+                f"{grp}: parsers.{module_name}.{entry} cannot be called with a "
+                f"single .eml path argument: {exc}") from exc
+        resolved[grp] = fn
+    return resolved
+
+
+# Bound once at cold start; a broken deploy fails the invocation here.
+_ENTRYPOINTS: dict[str, Any] = _resolve_entrypoints()
+
+
+def _validate_result(grp: str, result: Any) -> Any:
+    """Assert the parser returned the shared receipt schema.
+
+    A wrong return TYPE (e.g. a bare string) is an integration defect, not a
+    per-message parse failure: without this guard it would surface downstream
+    as an ``AttributeError`` on ``parsed.get(...)`` and be swallowed as
+    ``parse_error``. Raise ParserContractError so it propagates to the DLQ.
+    """
+    if result is None or isinstance(result, dict):
+        return result
+    if isinstance(result, list):
+        if all(isinstance(x, dict) for x in result):
+            return result
+        raise ParserContractError(
+            f"{grp}: parser returned a list with non-dict elements")
+    raise ParserContractError(
+        f"{grp}: parser returned {type(result).__name__}, expected dict/list/None")
+
+
+def run_parser(grp: str, eml_path: str) -> Any:
+    # Entry point resolved + signature-validated at cold start; return type
+    # validated here. Both raise ParserContractError (never a per-message parse
+    # error) so integration defects reach the DLQ instead of masquerading as a
+    # successful parse_error.
+    return _validate_result(grp, _ENTRYPOINTS[grp](eml_path))
+
+
+def classify(grp: str, subject: str, parsed: Any) -> str:
+    """-> 'receipt' | 'txn_signal' | 'needs_ocr' | 'non_receipt'."""
+    if isinstance(parsed, list):
+        parsed = parsed[0] if parsed else {}
+    parsed = parsed or {}
+    if grp == "chase-alerts":
+        # Only a recognized alert with a direction and amount is a usable
+        # reconciliation signal. Marketing, security notices, and malformed or
+        # spoofed mail that the parser could not classify are NOT signals.
+        if (parsed.get("alert_type") and parsed.get("direction")
+                and parsed.get("grand_total") is not None):
+            return "txn_signal"
+        return "non_receipt"
+    if grp == "uber" and parsed.get("_trip_summary_no_payment"):
+        # 2024+ Uber "trip summary" emails carry a fare total but state "This is
+        # not a payment receipt". Honor the parser's hint over the bare total so
+        # a non-charge is not emitted as a purchase.
+        return "non_receipt"
+    if grp == "venmo" and parsed.get("transaction_kind") in (
+            "p2p_sent", "p2p_received"):
+        # Peer-to-peer transfers are money movement, not merchant receipts. Only
+        # a transfer with a real amount AND direction is a usable reconciliation
+        # signal — subject-only matches (e.g. "You sent money on iMessage")
+        # carry no amount and would emit an unusable signal, so drop them the
+        # same way Chase alerts require direction + grand_total. merchant_purchase
+        # falls through to the normal receipt path below.
+        if (parsed.get("grand_total") is not None
+                and parsed.get("direction")):
+            return "txn_signal"
+        return "non_receipt"
+    if parsed.get("needs_pdf") or parsed.get("needs_ocr"):
+        return "needs_ocr"
+    gate = SUBJECT_DROP.get(grp)
+    if gate and gate.search(subject or ""):
+        return "non_receipt"
+    if parsed.get("grand_total") is None:
+        return "non_receipt"
+    return "receipt"
```
### infra/email_receipt_inbox/infrastructure.py
```
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
            # Public ingress: cap this function's slice of the account's shared
            # concurrency so a mail flood cannot starve every other Lambda.
            # Throttled async S3 invokes retry, so the cap loses no mail.
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
        invoke_perm = aws.lambda_.Permission(
            f"{name}-parser-s3-invoke",
            action="lambda:InvokeFunction",
            function=self.parser.name,
            principal="s3.amazonaws.com",
            source_arn=self.bucket.arn,
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
        })
```
### infra/email_receipt_inbox/lambdas/handler.py
```
"""S3-triggered parser for inbound receipt emails.

SES writes each inbound message to s3://<bucket>/raw/<messageId>. This handler
parses it through the sender registry and writes a result document to
parsed/<messageId>.<ingest_id>.json:

    {
      "message_id": "...",        # RFC-822 Message-ID (falls back to S3 key)
      "ingest_id": "...",         # sha256 of the raw bytes — the stable, non-
                                  # spoofable identity keying the parsed object
      "s3_key": "raw/...",
      "from_domain": "doordash.com",
      "original_from": "...",     # unwrapped when the mail arrived via a
                                  # forwarding rule (X-Forwarded-For et al.)
      "subject": "...",
      "group": "doordash" | null,
      "classification": "receipt" | "txn_signal" | "needs_ocr" | "non_receipt"
                        | "quarantine" | "unknown_sender" | "parse_error",
      "receipt": {...} | null,    # the parser's raw schema output
      "error": "..." | null
    }

The private reconciliation plane polls parsed/ and MUST ingest idempotently by
``ingest_id`` (the content digest): S3 notifications are unordered, so keying by
message_id/S3 key alone would let an older replay clobber a newer parse. Each
distinct message body maps to exactly one parsed object. This function
deliberately writes derived data only — no DynamoDB coupling — so re-parses are
a matter of re-running over raw/.
"""
from __future__ import annotations

import email
import email.errors
import email.policy
import email.utils
import hashlib
import json
import os
import re
import tempfile
import traceback
import urllib.parse

import boto3

import registry

s3 = boto3.client("s3")

# SES delivers S3-stored messages up to 40 MB, but receipt emails are a few
# hundred KB. This 256 MB function holds the raw bytes, the parsed MIME tree, a
# normalized copy, and parser-specific representations at once, so a large
# payload can OOM. Reject anything oversized BEFORE fetching it into memory.
MAX_RAW_BYTES = 15 * 1024 * 1024


def _from_domain(msg) -> tuple[str, str]:
    """Return (from_addr, domain) for sender dispatch.

    ``Reply-To`` is deliberately NOT consulted: it is free-form and often
    points marketing/spoofed mail at an unrelated domain. ``X-Original-From``
    is honored only for the documented iCloud auto-forwarding case (the
    forwarder rewrites ``From`` to itself but preserves the original sender
    here); the SES authentication verdicts recorded alongside let the
    downstream reconciliation plane decide how much to trust it.
    """
    for header in ("X-Original-From", "From"):
        raw = msg.get(header)
        if not raw:
            continue
        addr = email.utils.parseaddr(raw)[1]
        m = re.search(r"@([\w.\-]+)", addr or "")
        if m:
            return addr, m.group(1).lower()
    return "", ""


def _ses_auth(msg) -> dict:
    """Extract the SES-stamped spam/virus verdicts and SPF/DKIM/DMARC results.

    SES writes these headers into the stored message; scan_enabled only
    *produces* them, it never rejects, so the pipeline must gate on them.
    """
    spam = (msg.get("X-SES-Spam-Verdict") or "").strip().upper() or None
    virus = (msg.get("X-SES-Virus-Verdict") or "").strip().upper() or None
    ar = " ".join(msg.get_all("Authentication-Results") or [])
    auth = {}
    for mech in ("spf", "dkim", "dmarc"):
        m = re.search(rf"\b{mech}=(\w+)", ar)
        if m:
            auth[mech] = m.group(1).lower()
    return {"spam_verdict": spam, "virus_verdict": virus, "auth": auth}


def _content_rejected(spam, virus) -> bool:
    """Fail CLOSED on indeterminate SES content scans.

    SES emits PASS / FAIL / GRAY / PROCESSING_FAILED, and a verdict may be
    absent entirely if the message could not be scanned. Only an explicit
    ``PASS`` clears the virus scan — GRAY, PROCESSING_FAILED, FAIL, or a missing
    verdict all mean SES never affirmed the payload is clean, so quarantine.
    For spam, an explicit FAIL is spam and PROCESSING_FAILED means SES could not
    scan (e.g. malformed MIME); GRAY (borderline) is allowed through with the
    verdict recorded so downstream can weigh it.
    """
    if virus != "PASS":
        return True
    if spam in ("FAIL", "PROCESSING_FAILED"):
        return True
    return False


def lambda_handler(event, _context):
    results = []
    for record in event.get("Records", []):
        obj = record["s3"]["object"]
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(obj["key"])
        version_id = obj.get("versionId")
        out = {"s3_key": key, "version_id": version_id,
               "etag": obj.get("eTag"), "sequencer": obj.get("sequencer"),
               "group": None, "classification": "unknown_sender",
               "receipt": None, "error": None}

        # Size guard BEFORE any fetch: an oversized payload is quarantined
        # without ever loading its body into memory. Identity falls back to the
        # S3 ETag since we deliberately never hash the (unread) bytes; the
        # object is still recorded under parsed/ rather than silently dropped.
        size = obj.get("size")
        if size is not None and size > MAX_RAW_BYTES:
            ident = (obj.get("eTag") or "oversized").strip('"')
            out["ingest_id"] = ident
            out["classification"] = "quarantine"
            out["error"] = f"oversized: {size} bytes exceeds {MAX_RAW_BYTES}"
            dest = ("parsed/" + key.split("/", 1)[-1] + "." + ident + ".json")
            s3.put_object(
                Bucket=bucket, Key=dest,
                Body=json.dumps(out, ensure_ascii=False, default=str).encode(),
                ContentType="application/json")
            results.append({"key": key,
                            "classification": out["classification"]})
            continue

        # Infra fetch stays OUTSIDE the parse-error catch: throttling, transient
        # GetObject failures, and missing-object races must raise so Lambda
        # retries them (and, exhausted, land in the DLQ) rather than being
        # persisted as a permanent parse_error + reported as success.
        get_kwargs = {"Bucket": bucket, "Key": key}
        if version_id:
            get_kwargs["VersionId"] = version_id
        raw = s3.get_object(**get_kwargs)["Body"].read()
        # Immutable identity: content digest, independent of the sender's
        # spoofable RFC Message-ID (which is retained separately below).
        out["ingest_id"] = hashlib.sha256(raw).hexdigest()

        try:
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            out["message_id"] = (msg.get("Message-ID") or key).strip()
            out["subject"] = msg.get("Subject", "")
            out.update(_ses_auth(msg))
            from_addr, domain = _from_domain(msg)
            out["from_domain"] = domain
            out["original_from"] = from_addr
            if _content_rejected(spam=out["spam_verdict"],
                                 virus=out["virus_verdict"]):
                # SES flagged (or could not clear) malware/spam; hold it out of
                # reconciliation. Distinct from parser-determined non_receipt so
                # downstream can treat scan failures differently.
                out["classification"] = "quarantine"
            else:
                grp = registry.group_for_domain(domain)
                out["group"] = grp
                if grp:
                    # The registry chose the group from the canonical sender
                    # (X-Original-From in the iCloud-forwarding case), but the
                    # parsers re-dispatch on the message's own ``From`` header —
                    # which the forwarder rewrote to itself. Normalize ONLY the
                    # parser's temp copy so its From matches the sender the
                    # registry keyed on; the stored raw/ evidence is untouched.
                    parser_bytes = raw
                    orig_from = msg.get("X-Original-From")
                    if orig_from:
                        norm = email.message_from_bytes(
                            raw, policy=email.policy.default)
                        if norm.get("From") is not None:
                            norm.replace_header("From", orig_from)
                        else:
                            norm["From"] = orig_from
                        parser_bytes = norm.as_bytes()
                    with tempfile.NamedTemporaryFile(
                            suffix=".eml", delete=False) as tmp:
                        tmp.write(parser_bytes)
                        path = tmp.name
                    try:
                        parsed = registry.run_parser(grp, path)
                        if isinstance(parsed, list):
                            parsed = parsed[0] if parsed else {}
                        out["classification"] = registry.classify(
                            grp, out["subject"], parsed)
                        if out["classification"] in ("receipt", "txn_signal"):
                            out["receipt"] = parsed
                    finally:
                        os.unlink(path)
        except (ValueError, KeyError, IndexError, AttributeError, TypeError,
                email.errors.MessageError):
            # Deterministic email/parser validation failures on THIS message
            # body are permanent: recording parse_error and moving on is
            # correct — a retry would fail identically. Integration defects are
            # NOT reachable here: the parser entry points are resolved and
            # signature-checked at cold start and their return type is validated
            # in run_parser, so a missing/renamed entry point, a bad signature,
            # or a wrong return type raises registry.ParserContractError (not a
            # subclass of the exceptions above) and propagates. Operational
            # failures — ImportError (broken deploy), OSError (temp-file/disk),
            # and anything else unexpected — likewise propagate so Lambda
            # retries them and, once exhausted, routes the event to the DLQ
            # instead of persisting a false parse_error and reporting success.
            out["classification"] = "parse_error"
            out["error"] = traceback.format_exc(limit=4)

        # put_object is likewise retryable — keep it outside the catch.
        # Key by content digest, not the raw key: two versions of the same S3
        # key (a replay of the same messageId) would otherwise collide on one
        # parsed object, and unordered notifications could let the older version
        # win. ingest_id makes each distinct body its own idempotent object.
        dest = ("parsed/" + key.split("/", 1)[-1] + "."
                + out["ingest_id"] + ".json")
        s3.put_object(
            Bucket=bucket, Key=dest,
            Body=json.dumps(out, ensure_ascii=False, default=str).encode(),
            ContentType="application/json")
        results.append({"key": key, "classification": out["classification"]})
    return {"processed": results}
```
### infra/email_receipt_inbox/lambdas/registry.py
```
"""Sender-domain -> parser registry for inbound email receipts.

Parsers are stdlib-only modules exposing ``parse(path) -> dict`` (or
``parse_eml``) that take an RFC-822 .eml file path and return the shared
receipt schema (dollars as floats, ``grand_total is None`` for non-receipts).
"""
from __future__ import annotations

import importlib
import inspect
import re
from typing import Any, Optional


class ParserContractError(Exception):
    """A parser *integration* / return-contract violation.

    Distinct from a per-message parse failure. Raised for systematic defects —
    a missing or non-callable entry point, a signature that cannot accept the
    single .eml path argument, or a return value that is not the shared receipt
    schema (dict / list-of-dicts / None). These must PROPAGATE so Lambda
    retries and, once exhausted, routes the event to the DLQ, rather than being
    caught per-message and persisted as a false ``parse_error`` reported as
    success. Deliberately not a subclass of ValueError/TypeError so the
    handler's per-message parse catch never swallows it.
    """

# group -> (domain suffixes, module name, entry attr)
GROUPS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "apple": (("email.apple.com", "applepay.apple.com", "orders.apple.com", "apple.com"),
              "parse_apple", "parse"),
    "doordash": (("doordash.com",), "parse_doordash", "parse_eml"),
    "amazon": (("amazon.com",), "parse_amazon", "parse"),
    "venmo": (("venmo.com",), "parse_venmo", "parse_eml"),
    "paypal": (("paypal.com",), "parse_paypal", "parse_eml"),
    "pos-restaurants": (("toasttab.com", "squareup.com", "square.com", "clover.com",
                         "spoton.com"), "parse_pos_restaurants", "parse_eml"),
    "uber": (("uber.com",), "parse_uber", "parse_eml"),
    "retail": (("target.com", "bestbuy.com", "ebay.com", "starbucks.com"),
               "parse_retail", "parse"),
    "equinox": (("equinox.com",), "parse_equinox", "parse"),
    "github": (("github.com",), "parse_github", "parse"),
    "restaurant-platforms": (("chownow.com", "dylish.com", "oftendining.com"),
                             "parse_restaurant_platforms", "parse"),
    "sce": (("scewebservices.com",), "parse_restaurant_platforms", "parse"),
    "services": (("socalgas.com", "digitalocean.com", "stripe.com",
                  "accounts.nintendo.com"), "parse_services", "parse"),
    "chase-alerts": (("chase.com",), "parse_chase_alerts", "parse"),
    "costco": (("costco.com", "costco.com.mx"), "parse_costco", "parse"),
    "travel-housing": (("airbnb.com", "tesla.com", "hellolanding.com",
                        "landing.com"), "parse_travel_housing", "parse"),
}

# Notification/marketing templates whose bodies carry amounts that are NOT
# purchase receipts (kept in sync with the private reconciliation plane).
SUBJECT_DROP = {
    "paypal": re.compile(r"transfer request|requested a hold|hold on the funds|"
                         r"has been (removed|released)|policy|survey", re.I),
    "retail": re.compile(r"is ending soon|relisted item|sent a message|watched item|"
                         r"back in stock|price drop|\bbid\b|offer (received|declined|accepted)|"
                         r"pick up where you left|invite|coupon|% off|\bsale\b|\bdeals?\b", re.I),
    "sce": re.compile(r"bill is ready", re.I),
    "services": re.compile(r"bill is ready", re.I),
}


def group_for_domain(domain: Optional[str]) -> Optional[str]:
    d = (domain or "").lower()
    for grp, (suffixes, _, _) in GROUPS.items():
        for s in suffixes:
            if d == s or d.endswith("." + s):
                return grp
    return None


def _resolve_entrypoints() -> dict[str, Any]:
    """Import every parser module and bind its callable entry point ONCE, at
    cold start.

    A broken deploy — a missing parser module (ImportError), a missing/renamed
    entry attribute, or an entry that cannot be called with a single .eml path
    — fails HERE, at import time, so the whole invocation errors and the event
    retries / lands in the DLQ. Previously these surfaced only when a message
    happened to route to the broken group, inside the handler's per-message
    catch, where they were masked as a permanent ``parse_error`` and reported
    as success. Resolving up front turns a silent, per-message data-loss bug
    into a loud deploy failure.
    """
    resolved: dict[str, Any] = {}
    for grp, (_, module_name, entry) in GROUPS.items():
        mod = importlib.import_module(f"parsers.{module_name}")
        fn = getattr(mod, entry, None)
        if not callable(fn):
            raise ParserContractError(
                f"{grp}: parsers.{module_name}.{entry} is missing or not callable")
        try:
            inspect.signature(fn).bind("<eml-path>")
        except TypeError as exc:
            raise ParserContractError(
                f"{grp}: parsers.{module_name}.{entry} cannot be called with a "
                f"single .eml path argument: {exc}") from exc
        resolved[grp] = fn
    return resolved


# Bound once at cold start; a broken deploy fails the invocation here.
_ENTRYPOINTS: dict[str, Any] = _resolve_entrypoints()


def _validate_result(grp: str, result: Any) -> Any:
    """Assert the parser returned the shared receipt schema.

    A wrong return TYPE (e.g. a bare string) is an integration defect, not a
    per-message parse failure: without this guard it would surface downstream
    as an ``AttributeError`` on ``parsed.get(...)`` and be swallowed as
    ``parse_error``. Raise ParserContractError so it propagates to the DLQ.
    """
    if result is None or isinstance(result, dict):
        return result
    if isinstance(result, list):
        if all(isinstance(x, dict) for x in result):
            return result
        raise ParserContractError(
            f"{grp}: parser returned a list with non-dict elements")
    raise ParserContractError(
        f"{grp}: parser returned {type(result).__name__}, expected dict/list/None")


def run_parser(grp: str, eml_path: str) -> Any:
    # Entry point resolved + signature-validated at cold start; return type
    # validated here. Both raise ParserContractError (never a per-message parse
    # error) so integration defects reach the DLQ instead of masquerading as a
    # successful parse_error.
    return _validate_result(grp, _ENTRYPOINTS[grp](eml_path))


def classify(grp: str, subject: str, parsed: Any) -> str:
    """-> 'receipt' | 'txn_signal' | 'needs_ocr' | 'non_receipt'."""
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}
    parsed = parsed or {}
    if grp == "chase-alerts":
        # Only a recognized alert with a direction and amount is a usable
        # reconciliation signal. Marketing, security notices, and malformed or
        # spoofed mail that the parser could not classify are NOT signals.
        if (parsed.get("alert_type") and parsed.get("direction")
                and parsed.get("grand_total") is not None):
            return "txn_signal"
        return "non_receipt"
    if grp == "uber" and parsed.get("_trip_summary_no_payment"):
        # 2024+ Uber "trip summary" emails carry a fare total but state "This is
        # not a payment receipt". Honor the parser's hint over the bare total so
        # a non-charge is not emitted as a purchase.
        return "non_receipt"
    if grp == "venmo" and parsed.get("transaction_kind") in (
            "p2p_sent", "p2p_received"):
        # Peer-to-peer transfers are money movement, not merchant receipts. Only
        # a transfer with a real amount AND direction is a usable reconciliation
        # signal — subject-only matches (e.g. "You sent money on iMessage")
        # carry no amount and would emit an unusable signal, so drop them the
        # same way Chase alerts require direction + grand_total. merchant_purchase
        # falls through to the normal receipt path below.
        if (parsed.get("grand_total") is not None
                and parsed.get("direction")):
            return "txn_signal"
        return "non_receipt"
    if parsed.get("needs_pdf") or parsed.get("needs_ocr"):
        return "needs_ocr"
    gate = SUBJECT_DROP.get(grp)
    if gate and gate.search(subject or ""):
        return "non_receipt"
    if parsed.get("grand_total") is None:
        return "non_receipt"
    return "receipt"
```
## Decision log
# Review Round 1 — Resolution

1. [high] FIXED — Hardened the SES→S3→Lambda creation graph. Added the
   `_amazonses.<subdomain>` verification TXT record + `DomainIdentityVerification`
   (identity is now actually verified before use; record stays on the `in.`
   subdomain). Retained resource handles and wired dependencies: the Lambda
   `depends_on` its IAM policies, the `BucketNotification` `depends_on` the
   invoke `Permission`, the `ReceiptRule` `depends_on` the identity verification
   and the bucket policy, and `ActiveReceiptRuleSet` `depends_on` the store rule
   (no briefly-empty active set).

2. [high] FIXED (partial) — Removed `Reply-To` from sender dispatch (free-form,
   spoofable); kept `X-Original-From`→`From` for the documented iCloud
   forwarding case. Added `_ses_auth()` to capture SES `X-SES-Spam-Verdict`,
   `X-SES-Virus-Verdict`, and SPF/DKIM/DMARC from `Authentication-Results` into
   the parsed output; spam/virus `FAIL` is now short-circuited to `non_receipt`
   so it never reaches reconciliation. DECLINED the SES-level hard reject/bounce
   on DKIM/DMARC alignment: this pipeline is designed around iCloud
   auto-forwarding, which rewrites the envelope and breaks SPF/DMARC *alignment*
   by construction (authenticated `From` becomes the forwarder, not chase.com),
   so an alignment-gated SES reject would drop exactly the legitimate forwarded
   receipts the feature ingests. The recorded verdicts let the private
   reconciliation plane gate on trust downstream instead.

3. [med] FIXED — `s3.get_object` and `s3.put_object` moved OUTSIDE the
   parse-error catch so throttling / transient GetObject / missing-object races
   re-raise and Lambda retries them (exhausted → DLQ) instead of being persisted
   as a permanent `parse_error` with a success return. Only deterministic
   email/parser validation now yields `parse_error`.

4. [med] FIXED — Added an SQS DLQ (14-day retention) plus a
   `FunctionEventInvokeConfig` on-failure destination (`maximum_retry_attempts=2`)
   and `sqs:SendMessage` in the execution role, so async invokes that exhaust
   retries are captured durably rather than discarded.

5. [med] FIXED — Enabled `BucketVersioning`; handler now reads `versionId`/
   `eTag`/`sequencer` from the event, fetches the exact `VersionId`, and records
   all three in the output. `ingest_id` is now a `sha256` content digest
   (immutable, not sender-controlled); the RFC `Message-ID` is retained
   separately. Added `s3:GetObjectVersion` to the role.

6. [med] FIXED (partial) — Registered `costco` (costco.com, costco.com.mx →
   `parse_costco.parse`) and `travel-housing` (airbnb.com, tesla.com,
   hellolanding.com, landing.com → `parse_travel_housing.parse`); added a
   library `parse(path)` entry point (via `_dispatch`) to the travel module so
   the registry can call it without argparse. DECLINED vendoring `pypdf`:
   `parse_costco._parse_gas_pdf` already degrades gracefully when `pypdf` is
   absent (`PdfReader is None` → `needs_ocr`/note), which is the reviewer's own
   suggested alternative (route Costco PDFs to OCR without it). Registry now has
   16 groups.

7. [med] DECLINED — Single-owner SES topology is a deployment decision, not a
   mechanical fix. The component is instantiated only behind
   `portfolio:email_receipt_inbox_enabled` (default false; no stack sets it), the
   rule-set name is already stack-scoped (`f"{name}-{stack}"` → distinct sets per
   stack), and the module docstring already CAUTIONS that SES allows one active
   rule set per account+region. The only true singleton is activation, which the
   caution documents; deciding which stack owns `in.tylernorlund.com`
   identity/MX/activation is an ops/topology call that can't be resolved in code
   without picking that owner, and doing so unilaterally would violate the
   config-gated-instantiation contract.

8. [med] FIXED — `classify()` no longer returns `txn_signal` unconditionally for
   `chase-alerts`. It now requires a recognized `alert_type`, a `direction`, and
   a non-null `grand_total`; marketing/security/malformed/spoofed Chase mail the
   parser could not classify returns `non_receipt`.

9. [med] FIXED — Set `tls_policy="Require"` on the receipt rule (was defaulting
   to Optional/opportunistic TLS for financial mail).

10. [low] FIXED (partial) — When `raw_retention_days` is configured, the
    lifecycle now covers BOTH `raw/` and `parsed/` and expires noncurrent
    versions (needed now that versioning is on). DECLINED hard-coding a default
    retention duration (data-governance decision — mechanism stays opt-in) and
    the object-count/bytes CloudWatch alarms (non-trivial for a [low]; advisory).
# Review Round 2 — Resolution

1. [med] FIXED — Hardened the delivery-vs-storage creation ordering. Captured
   the `BucketVersioning` and `BucketNotification` handles. `BucketLifecycleConfiguration`
   now `depends_on=[versioning]` (noncurrent-version expiry is meaningless on an
   unversioned bucket). `ActiveReceiptRuleSet` now `depends_on=[store_rule,
   versioning, notification]`. The inbound MX record was moved to the END of
   `__init__` and `depends_on` the activation (when `activate=True`) plus
   `store_rule`, `versioning`, and `notification` — so mail can only be accepted
   once the versioned bucket, the S3→Lambda trigger, and the active rule set all
   exist.

2. [med] FIXED — Parsed objects are now keyed by content digest:
   `parsed/<name>.<ingest_id>.json` instead of `parsed/<name>.json`. Two versions
   of the same raw S3 key (a replay of the same messageId) can no longer collide
   on one parsed object, so an unordered older replay cannot clobber a newer
   parse. Updated the module docstring to require downstream idempotency on
   `ingest_id` (the sha256 content digest) rather than the sender-controlled
   `message_id`.

3. [med] FIXED — Integration break between registry dispatch (canonical
   `X-Original-From`) and the per-domain parsers (which re-dispatch on the
   message's own `From`, rewritten to the forwarder in the iCloud case). When
   `X-Original-From` is present, the handler now normalizes ONLY the parser's
   temp `.eml` copy — rewriting its `From` to the original sender — so
   `parse_retail`/`parse_services`/`parse_pos_restaurants` etc. dispatch to the
   correct brand instead of falling to the empty else branch. The stored `raw/`
   evidence is untouched; parsers themselves are unchanged.

4. [med] FIXED — Content scanning now fails CLOSED. New `_content_rejected()`
   helper: virus verdict must be an explicit `PASS` (GRAY / PROCESSING_FAILED /
   FAIL / missing all quarantine), and spam `FAIL` or `PROCESSING_FAILED`
   quarantine (spam `GRAY` borderline still allowed through with the verdict
   recorded). Rejected mail gets a new `quarantine` classification, distinct
   from parser-determined `non_receipt`, and never populates `receipt`. The
   reviewer's preferred receipt-action-metadata source is a different trigger
   architecture (this Lambda is S3-triggered and only sees stored headers); the
   "at least" header-based gate they requested is implemented.

5. [low] FIXED — Gave the receipt rule set and rule deterministic physical
   names (`<name>-<stack>`, `<name>-store-<stack>`) and added an
   `aws:SourceArn` StringEquals condition (alongside the existing
   `aws:SourceAccount`) pinning the SES `s3:PutObject` grant under `raw/` to
   that one receipt rule, closing the account-wide confused-deputy gap.

6. [deadlock] ESCALATED — Public-ingress trust / provenance policy. Per loop
   protocol, deadlock findings are not implemented or argued here; escalated to
   a human.

7. [deadlock] ESCALATED — Costco gas-ticket `needs_ocr` on missing `pypdf`.
   Per loop protocol, deadlock findings are not implemented or argued here;
   escalated to a human. (Note: also constrained by the "parsers untouched
   unless integration break" design contract.)
# Review Round 3 — Resolution

1. [med] FIXED — Captured `FunctionEventInvokeConfig` as `async_config` and added it to the S3 `BucketNotification` `depends_on`. Since the rule-set activation and MX record both depend on the notification, this propagates readiness to the entire mail-accepting graph — no delivered message can invoke the parser before the DLQ failure destination exists. Also added `maximum_event_age_in_seconds=3600` to bound the retry window.

2. [med] FIXED — Added a pre-fetch size guard in `handler.py` (`MAX_RAW_BYTES = 15 MB`): oversized payloads are quarantined and recorded under `parsed/` (identity from the S3 ETag) without ever loading the body into memory. Set `reserved_concurrent_executions=10` on the parser to cap its slice of shared account concurrency (throttled async S3 invokes retry, so no mail is lost), and added `maximum_event_age_in_seconds=3600`.

3. [med] FIXED — `registry.classify` now honors parser-provided semantics: Uber messages with `_trip_summary_no_payment` ("This is not a payment receipt") map to `non_receipt`; Venmo `transaction_kind` of `p2p_sent`/`p2p_received` map to `txn_signal`. `merchant_purchase` falls through to the normal receipt path. Parsers untouched — only the integration classifier reads the hints they already emit.

4. [med] FIXED — Narrowed the handler's `except` from bare `Exception` to `(ValueError, KeyError, IndexError, AttributeError, TypeError, email.errors.MessageError)`. Deterministic parse/validation failures still record `parse_error`; `ImportError` (broken deploy), `OSError` (temp-file/disk), and any other unexpected exception now propagate to Lambda retry handling and, once exhausted, the DLQ.
# Review Round 4 — Resolution

1. [high] FIXED — `handler.py` called `_content_rejected(out["virus_verdict"], out["spam_verdict"])` against the `(spam, virus)` signature, so the args were swapped: the `virus != "PASS"` fail-closed check ran on the spam verdict and the FAIL/PROCESSING_FAILED check ran on the virus verdict. A missing/GRAY virus verdict passed through while a benign non-PASS spam verdict was quarantined. Now called with explicit keywords `_content_rejected(spam=out["spam_verdict"], virus=out["virus_verdict"])`. Verified the verdict matrix: virus GRAY/missing → quarantine, virus PASS + spam GRAY → allow, virus PASS + spam FAIL → quarantine.

2. [med] FIXED — `registry.classify` Venmo branch now requires a non-null `grand_total` AND a valid `direction` before emitting `txn_signal`, mirroring the Chase-alert gate. Subject-only matches with no amount (e.g. "You sent money on iMessage", which the parser classifies as `p2p_sent` but leaves `grand_total=None`) now map to `non_receipt` instead of producing an unusable reconciliation signal. `merchant_purchase` still falls through to the normal receipt path. Classifier-only change — parsers untouched. Verified: no-amount p2p → non_receipt, p2p with amount → txn_signal, merchant_purchase → receipt.

3. [low] FIXED — `infrastructure.py` published the inbound MX record unconditionally, even when `activate=False`, so mail would reach SES and be evaluated against whatever other rule set is active (which has no rule for this recipient) and be rejected/dropped. MX is now created only when this component activated its own rule set (`activation is not None`), or when the caller explicitly asserts the store rule was merged into an externally-managed active set via the new `external_active_rule_set` opt-in. Default `activate=True` behavior is unchanged. Trivial and a genuine correctness improvement, so implemented despite [low].
# Resolution — Review Round 5

1. [med] FIXED — Removed the broken `external_active_rule_set` escape hatch. The
   store rule is always created inside this component's own `rule_set`, so it can
   never be "merged" into a foreign externally-managed active set, and the boolean
   added no dependency on any external activation resource — MX could publish while
   mail routed to an unrelated active set and was dropped. The mode was also unused
   (default `False`; the sole caller `infra/__main__.py:1607` never passes it).
   Chose the "remove this mode" remediation over the external-rule-set redesign
   (option 2), which would add substantial complexity — creating the store rule
   inside a foreign set + threading an external activation resource — for a caller
   that does not exist. MX is now published only when `activation is not None`
   (i.e. this component activated its own rule set), and the MX record depends on
   that activation, versioning, notification, and the store rule.
# Resolution — Review Round 6

1. [med] handler.py:204 — broad AttributeError/TypeError catch swallows parser integration failures (missing entry point, wrong signature, invalid return type) as permanent parse_error, bypassing retries/DLQ — **FIXED**.
   - `registry._resolve_entrypoints()` imports every parser module and binds + `inspect.signature`-validates each entry point once at **cold start**. A missing module (ImportError), a missing/renamed/non-callable entry attr, or a signature that can't take the single .eml path now fails the whole invocation → retries → DLQ, instead of surfacing per-message inside the handler's catch as a false `parse_error`.
   - `run_parser` now uses the pre-resolved callable and validates the return type via `_validate_result` (dict / list-of-dicts / None); a wrong return type raises the new `ParserContractError`.
   - `ParserContractError` subclasses `Exception` only (not ValueError/TypeError/etc.), so it propagates past the handler's per-message parse catch to the DLQ.
   - Partial DECLINE (sub-recommendation): the reviewer's "catch only a dedicated per-message parsing exception" would require the untouched stdlib parsers to raise a bespoke exception type — barred by the "parsers untouched" design contract and unnecessary once the three integration paths are structurally removed. The remaining ValueError/KeyError/IndexError/AttributeError/TypeError/MessageError catch now covers only genuine parser-internal failures on THIS message body, which are correctly permanent parse_errors. Handler comment updated to document this.

Verification: `py_compile` of infrastructure.py, handler.py, registry.py passes; `import registry` (which now eagerly resolves all 16 group entry points) succeeds with 16 groups; return-type validation unit-checks pass.
## Your previous review (round 6)
1. [med] `infra/email_receipt_inbox/lambdas/handler.py:204` — The broad `AttributeError`/`TypeError` catch also swallows parser integration failures, including a missing registry entry point, wrong function signature, or invalid parser return type. Those failures are persisted as successful `parse_error` results, bypassing Lambda retries and the DLQ. Resolve and validate parser callables at cold start outside this catch, validate returned values explicitly, and catch only a dedicated per-message parsing exception for terminal input errors.
