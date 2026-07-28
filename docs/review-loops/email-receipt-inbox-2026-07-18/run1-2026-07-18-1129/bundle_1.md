# Adversarial code review — round 1 of an automated fix loop

You are reviewing a Pulumi (Python) PR for tnorlund/Portfolio: an SES inbound email pipeline (receipts@ -> SES rule -> S3 raw/ -> parser Lambda -> S3 parsed/ JSON). Review for: infra correctness (SES/S3/Lambda/IAM/Route53 wiring and ordering), security (this address is publicly emailable), idempotency/replay behavior, failure modes and retry semantics, and Lambda handler correctness.

OUT OF SCOPE: The vendored parsers under lambdas/parsers/ are pre-tested elsewhere; only flag integration-level issues with them (imports, interface mismatches), not their internals.

Process: your findings are implemented (or declined with justification)
by an automated implementer; its cumulative decision log is below. Do
not repeat addressed findings. If you flagged an issue before, it was
declined with justification, and you still believe you are right,
tag it [deadlock] instead of re-arguing — deadlocks go to a human.

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
index 000000000..aa2d3fce9
--- /dev/null
+++ b/infra/email_receipt_inbox/infrastructure.py
@@ -0,0 +1,201 @@
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
+        # --- SES identity + DKIM + inbound MX on the isolated subdomain
+        identity = aws.ses.DomainIdentity(f"{name}-identity", domain=domain,
+                                          opts=child)
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
+        aws.route53.Record(
+            f"{name}-mx",
+            zone_id=zone.zone_id,
+            name=domain,
+            type="MX",
+            ttl=300,
+            records=[f"10 inbound-smtp.{region}.amazonaws.com"],
+            opts=child)
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
+        if raw_retention_days:
+            aws.s3.BucketLifecycleConfiguration(
+                f"{name}-mail-lifecycle",
+                bucket=self.bucket.id,
+                rules=[{"id": "expire-raw", "status": "Enabled",
+                        "filter": {"prefix": "raw/"},
+                        "expiration": {"days": raw_retention_days}}],
+                opts=child)
+        aws.s3.BucketPolicy(
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
+                        "Condition": {"StringEquals": {
+                            "aws:SourceAccount": a[1]}},
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
+        aws.iam.RolePolicyAttachment(
+            f"{name}-parser-logs",
+            role=role.name,
+            policy_arn=aws.iam.ManagedPolicy.AWS_LAMBDA_BASIC_EXECUTION_ROLE,
+            opts=child)
+        aws.iam.RolePolicy(
+            f"{name}-parser-s3",
+            role=role.id,
+            policy=self.bucket.arn.apply(lambda arn: pulumi.Output.json_dumps({
+                "Version": "2012-10-17",
+                "Statement": [
+                    {"Effect": "Allow", "Action": ["s3:GetObject"],
+                     "Resource": f"{arn}/raw/*"},
+                    {"Effect": "Allow", "Action": ["s3:PutObject"],
+                     "Resource": f"{arn}/parsed/*"},
+                ],
+            })),
+            opts=child)
+        self.parser = aws.lambda_.Function(
+            f"{name}-parser",
+            runtime="python3.12",
+            handler="handler.lambda_handler",
+            role=role.arn,
+            timeout=60,
+            memory_size=256,
+            code=pulumi.AssetArchive({
+                ".": pulumi.FileArchive(LAMBDA_DIR),
+            }),
+            tags=tags,
+            opts=child)
+        aws.lambda_.Permission(
+            f"{name}-parser-s3-invoke",
+            action="lambda:InvokeFunction",
+            function=self.parser.name,
+            principal="s3.amazonaws.com",
+            source_arn=self.bucket.arn,
+            opts=child)
+        aws.s3.BucketNotification(
+            f"{name}-mail-notify",
+            bucket=self.bucket.id,
+            lambda_functions=[{
+                "lambda_function_arn": self.parser.arn,
+                "events": ["s3:ObjectCreated:*"],
+                "filter_prefix": "raw/",
+            }],
+            opts=ResourceOptions(parent=self, depends_on=[self.parser]))
+
+        # --- receipt rule set
+        rule_set = aws.ses.ReceiptRuleSet(
+            f"{name}-rules", rule_set_name=f"{name}-{stack}", opts=child)
+        aws.ses.ReceiptRule(
+            f"{name}-store-rule",
+            rule_set_name=rule_set.rule_set_name,
+            recipients=[self.address],
+            enabled=True,
+            scan_enabled=True,
+            s3_actions=[{
+                "bucket_name": self.bucket.bucket,
+                "object_key_prefix": "raw/",
+                "position": 1,
+            }],
+            opts=ResourceOptions(parent=self, depends_on=[rule_set]))
+        if activate:
+            aws.ses.ActiveReceiptRuleSet(
+                f"{name}-rules-active",
+                rule_set_name=rule_set.rule_set_name,
+                opts=ResourceOptions(parent=self, depends_on=[rule_set]))
+
+        self.register_outputs({
+            "address": self.address,
+            "bucket": self.bucket.bucket,
+            "parser_arn": self.parser.arn,
+        })
diff --git a/infra/email_receipt_inbox/lambdas/handler.py b/infra/email_receipt_inbox/lambdas/handler.py
new file mode 100644
index 000000000..1a10a6d96
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/handler.py
@@ -0,0 +1,99 @@
+"""S3-triggered parser for inbound receipt emails.
+
+SES writes each inbound message to s3://<bucket>/raw/<messageId>. This handler
+parses it through the sender registry and writes a result document to
+parsed/<messageId>.json:
+
+    {
+      "message_id": "...",        # RFC-822 Message-ID (falls back to S3 key)
+      "s3_key": "raw/...",
+      "from_domain": "doordash.com",
+      "original_from": "...",     # unwrapped when the mail arrived via a
+                                  # forwarding rule (X-Forwarded-For et al.)
+      "subject": "...",
+      "group": "doordash" | null,
+      "classification": "receipt" | "txn_signal" | "needs_ocr" | "non_receipt"
+                        | "unknown_sender" | "parse_error",
+      "receipt": {...} | null,    # the parser's raw schema output
+      "error": "..." | null
+    }
+
+The private reconciliation plane polls parsed/ and ingests idempotently by
+message_id. This function deliberately writes derived data only — no DynamoDB
+coupling — so re-parses are a matter of re-running over raw/.
+"""
+from __future__ import annotations
+
+import email
+import email.policy
+import email.utils
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
+
+def _from_domain(msg) -> tuple[str, str]:
+    """Return (from_addr, domain), preferring the ORIGINAL sender when the
+    message was auto-forwarded (iCloud rules keep it in these headers)."""
+    for header in ("X-Original-From", "Reply-To", "From"):
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
+def lambda_handler(event, _context):
+    results = []
+    for record in event.get("Records", []):
+        bucket = record["s3"]["bucket"]["name"]
+        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
+        out = {"s3_key": key, "group": None, "classification": "unknown_sender",
+               "receipt": None, "error": None}
+        try:
+            raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
+            msg = email.message_from_bytes(raw, policy=email.policy.default)
+            out["message_id"] = (msg.get("Message-ID") or key).strip()
+            out["subject"] = msg.get("Subject", "")
+            from_addr, domain = _from_domain(msg)
+            out["from_domain"] = domain
+            out["original_from"] = from_addr
+            grp = registry.group_for_domain(domain)
+            out["group"] = grp
+            if grp:
+                with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as tmp:
+                    tmp.write(raw)
+                    path = tmp.name
+                try:
+                    parsed = registry.run_parser(grp, path)
+                    if isinstance(parsed, list):
+                        parsed = parsed[0] if parsed else {}
+                    out["classification"] = registry.classify(
+                        grp, out["subject"], parsed)
+                    if out["classification"] in ("receipt", "txn_signal"):
+                        out["receipt"] = parsed
+                finally:
+                    os.unlink(path)
+        except Exception:
+            out["classification"] = "parse_error"
+            out["error"] = traceback.format_exc(limit=4)
+
+        dest = "parsed/" + key.split("/", 1)[-1] + ".json"
+        s3.put_object(
+            Bucket=bucket, Key=dest,
+            Body=json.dumps(out, ensure_ascii=False, default=str).encode(),
+            ContentType="application/json")
+        results.append({"key": key, "classification": out["classification"]})
+    return {"processed": results}
diff --git a/infra/email_receipt_inbox/lambdas/parsers/__init__.py b/infra/email_receipt_inbox/lambdas/parsers/__init__.py
new file mode 100644
index 000000000..e69de29bb
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_amazon.py b/infra/email_receipt_inbox/lambdas/parsers/parse_amazon.py
new file mode 100644
index 000000000..4549c15a3
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_amazon.py
@@ -0,0 +1,255 @@
+#!/usr/bin/env python3
+"""Prototype parser for Amazon order-confirmation emails -> receipt JSON.
+
+Handles the format eras observed in the mailbox:
+  A) 2015-2019 "Your Amazon.com order of \"X\"...": plaintext has
+     Total Before Tax / Estimated Tax / Order Total. One (truncated) item title.
+  B) 2020-2023 "Your Amazon.com order #...": de-itemized. Order # + Order Total only.
+  C) 2023-2024 "order of \"X\"" revival: truncated title + "Qty : N" + Order Total.
+  D) 2025-2026 "Ordered: ...": HTML has item titles (truncated), Quantity,
+     unit price + line total (split "$"/"29"/"95" cells), Grand Total / Total.
+     Plaintext hides all amounts, so HTML is required.
+  E) Amazon Fresh in-store receipt / "Thanks for shopping at Amazon":
+     Sub Total / Tax / Purchase Total / Total Items (no item lines).
+  F) Whole Foods "order has been received": only Payment Authorization (estimate).
+
+No Amazon order email carries card last4 or true payment-method detail.
+
+Usage: parse_amazon.py file.eml [file2.eml ...]   (prints one JSON per file)
+"""
+import sys, os, re, json, html as htmllib
+import email
+from email import policy
+
+MONEY = r"\$?\s*([\d,]+\.\d{2})"
+ZWJUNK = "​‌‍⁦⁧⁨⁩﻿­͏   ⠀"
+ORDER_ID = re.compile(r"\b((?:\d{3}|D01)-\d{7}-\d{7})\b")
+
+BOILER = (
+    "your orders", "your account", "buy again", "view or edit order",
+    "view or manage order", "order #", "ordered", "shipped", "out for delivery",
+    "delivered", "details", "order confirmation", "hello", "thanks for your order",
+    "thank you for shopping", "we hope to see you again", "amazon.com",
+    "ship to:", "arriving", "sold by", "questions?",
+)
+MARKETING_HEADERS = (
+    "top picks for you", "keep shopping for", "deals for you",
+    "customers also bought", "customers who bought", "also bought",
+    "recommended based on", "related to items",
+)
+
+
+def _decode_amount(s):
+    return float(s.replace(",", ""))
+
+
+def get_bodies(msg):
+    plain, html = "", ""
+    for part in msg.walk():
+        ct = part.get_content_type()
+        if ct not in ("text/plain", "text/html"):
+            continue
+        try:
+            content = part.get_content()
+        except Exception:
+            payload = part.get_payload(decode=True) or b""
+            content = payload.decode("utf-8", "replace")
+        if ct == "text/plain" and not plain:
+            plain = content
+        elif ct == "text/html" and not html:
+            html = content
+    return plain, html
+
+
+def html_to_lines(h):
+    h = re.sub(r"<(style|script).*?</\1>", " ", h, flags=re.S | re.I)
+    h = re.sub(r"<[^>]+>", "\n", h)
+    h = htmllib.unescape(h)
+    h = h.translate({ord(c): " " for c in ZWJUNK})
+    lines = [re.sub(r"\s+", " ", l).strip() for l in h.split("\n")]
+    return [l for l in lines if l]
+
+
+def find_total(text, labels):
+    """Find 'Label: $x.xx' (same line) in plaintext."""
+    for lab in labels:
+        m = re.search(re.escape(lab) + r"\s*:?\s*" + MONEY, text, re.I)
+        if m:
+            return _decode_amount(m.group(1))
+    return None
+
+
+def find_total_lines(lines, labels):
+    """Find label line, amount on same or one of next 2 lines (HTML layout)."""
+    for i, l in enumerate(lines):
+        for lab in labels:
+            if l.lower().startswith(lab.lower()):
+                m = re.search(MONEY, l)
+                if m:
+                    return _decode_amount(m.group(1))
+                for j in (i + 1, i + 2):
+                    if j < len(lines):
+                        m = re.fullmatch(MONEY, lines[j])
+                        if m:
+                            return _decode_amount(m.group(1))
+    return None
+
+
+def collect_prices_after(lines, i, limit=10):
+    """Collect decimal amounts following index i. Handles split '$'/'29'/'95'
+    cells and inline '$29.95' lines. Stops at a non-price, non-'$' line."""
+    prices, j = [], i + 1
+    while j < len(lines) and j <= i + limit:
+        l = lines[j]
+        m = re.fullmatch(MONEY, l)
+        if m:
+            prices.append(_decode_amount(m.group(1)))
+            j += 1
+            continue
+        if l == "$" and j + 2 < len(lines) and lines[j + 1].isdigit() and lines[j + 2].isdigit():
+            prices.append(float(f"{int(lines[j+1].replace(',',''))}.{int(lines[j+2]):02d}")
+                          if "," not in lines[j + 1]
+                          else float(lines[j + 1].replace(",", "") + "." + lines[j + 2]))
+            j += 3
+            continue
+        if l == "$" or l.isdigit():
+            j += 1
+            continue
+        break
+    return prices
+
+
+def is_boiler(l):
+    ll = l.lower()
+    return any(ll.startswith(b) for b in BOILER) or ORDER_ID.fullmatch(l.strip("‫‬ ")) \
+        or re.fullmatch(r"\d+", l)
+
+
+def parse_items_new_era(lines):
+    """Era D: title line precedes 'Quantity: N'; two prices follow (unit, line total)."""
+    items = []
+    # cut off marketing tail
+    cut = len(lines)
+    for i, l in enumerate(lines):
+        if any(l.lower().startswith(h) for h in MARKETING_HEADERS):
+            cut = i
+            break
+    lines = lines[:cut]
+    for i, l in enumerate(lines):
+        m = re.fullmatch(r"(?:Quantity|Qty)\s*:\s*(\d+)", l, re.I)
+        if not m:
+            continue
+        qty = int(m.group(1))
+        title = None
+        for j in range(i - 1, max(i - 4, -1), -1):
+            if not is_boiler(lines[j]) and not re.fullmatch(MONEY, lines[j]) and lines[j] != "$":
+                title = lines[j]
+                break
+        prices = collect_prices_after(lines, i)
+        unit = prices[0] if prices else None
+        line_total = prices[1] if len(prices) > 1 else (unit * qty if unit is not None else None)
+        items.append({"description": title, "quantity": qty,
+                      "unit_price": unit, "total": line_total})
+    return items
+
+
+def parse(path):
+    with open(path, "rb") as f:
+        msg = email.message_from_binary_file(f, policy=policy.default)
+    subject = str(msg.get("Subject", "") or "")
+    plain, html = get_bodies(msg)
+    lines = html_to_lines(html) if html else []
+    all_text = plain + "\n" + "\n".join(lines)
+
+    out = {
+        "source": "email",
+        "message_id": str(msg.get("Message-ID", "") or "").strip("<> "),
+        "sender_domain": "amazon.com",
+        "merchant_name": "Amazon.com",
+        "date": None, "order_id": None,
+        "grand_total": None, "subtotal": None, "tax": None, "tip": None,
+        "item_count": None, "items": [],
+        "payment_method": {"type": None, "card_last4": None},
+        "currency": "USD",
+        "raw_ref": {"mbox_file": None, "byte_offset": None},
+    }
+
+    try:
+        d = msg.get("Date")
+        if d:
+            out["date"] = email.utils.parsedate_to_datetime(str(d)).date().isoformat()
+    except Exception:
+        pass
+
+    m = ORDER_ID.search(all_text)
+    if m:
+        out["order_id"] = m.group(1)
+
+    sub_l = subject.lower()
+    if "whole foods" in sub_l or "whole foods" in all_text.lower()[:2000]:
+        out["merchant_name"] = "Whole Foods Market"
+    elif "fresh" in sub_l or "shopping at amazon fresh" in all_text.lower():
+        out["merchant_name"] = "Amazon Fresh"
+
+    # ---- totals (plaintext first: eras A/B/C/E carry values there) ----
+    out["subtotal"] = find_total(plain, ["Total Before Tax", "Sub Total", "Subtotal", "Item Subtotal"])
+    out["tax"] = find_total(plain, ["Estimated Tax", "Tax"])
+    out["grand_total"] = find_total(plain, ["Order Total", "Purchase Total", "Grand Total", "Order total"])
+    if out["grand_total"] is None:
+        out["grand_total"] = find_total(plain, ["Payment Authorization"])  # WF estimate
+    # HTML fallback (era D hides plaintext values)
+    if out["grand_total"] is None and lines:
+        out["grand_total"] = find_total_lines(lines, ["Grand Total", "Order Total", "Purchase Total", "Total"])
+    if out["subtotal"] is None and lines:
+        out["subtotal"] = find_total_lines(lines, ["Total Before Tax", "Sub Total", "Subtotal"])
+    if out["tax"] is None and lines:
+        out["tax"] = find_total_lines(lines, ["Estimated Tax", "Tax:"])
+
+    # ---- items ----
+    items = parse_items_new_era(lines) if lines else []
+    if not items:
+        # era A/C: one truncated title from subject or body
+        title, qty = None, 1
+        m = re.search(r'order of (\d+ x )?"(.+?)"', subject)
+        if m:
+            title = m.group(2)
+            if m.group(1):
+                qty = int(m.group(1).split()[0])
+        else:
+            m = re.search(r'Ordered:\s*(\d+)?\s*"(.+?)"', subject)
+            if m:
+                title = m.group(2)
+                qty = int(m.group(1)) if m.group(1) else 1
+            else:
+                m = re.search(r'You ordered\s+"(.+?)"', plain)
+                if m:
+                    title = m.group(1)
+        if title:
+            items = [{"description": title, "quantity": qty,
+                      "unit_price": None, "total": None}]
+        # subject may note additional hidden items: 'and N more item(s)'
+        m = re.search(r"and\s+.?(\d+).?\s+more item", subject)
+        if m:
+            for _ in range(int(m.group(1))):
+                items.append({"description": None, "quantity": 1,
+                              "unit_price": None, "total": None})
+
+    out["items"] = items
+    if items:
+        out["item_count"] = sum(i.get("quantity") or 1 for i in items)
+    else:
+        m = re.search(r"Total Items\s*:?\s*(\d+)", all_text)
+        if m:
+            out["item_count"] = int(m.group(1))
+
+    return out
+
+
+def main():
+    for path in sys.argv[1:]:
+        rec = parse(path)
+        print(json.dumps(rec, indent=2))
+
+
+if __name__ == "__main__":
+    main()
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_apple.py b/infra/email_receipt_inbox/lambdas/parsers/parse_apple.py
new file mode 100644
index 000000000..e02c3455e
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_apple.py
@@ -0,0 +1,311 @@
+#!/usr/bin/env python3
+"""Prototype parser for Apple receipt emails (email.apple.com, apple.com,
+applepay.apple.com, orders.apple.com).
+
+Handles:
+  A. App Store / subscription receipts, classic layout (~2013 - mid/late 2025):
+     "Receipt APPLE ID ... BILLED TO ... DATE ... ORDER ID ... TOTAL $x"
+  B. App Store / AppleCare receipts, new layout (late 2025+):
+     "Receipt <Month D, YYYY> Order ID: X Document: N ... Subtotal $x
+      Visa •••• 1234 (Apple Pay) $x"
+  C. Apple Cash payment receipts (applepay.apple.com).
+  D. Apple online-store order confirmations (orders.apple.com):
+     Subtotal / Estimated Tax / Order Total, items with Qty.
+  E. Apple retail-store receipts ("Your receipt from Apple <Store>"):
+     body is a thank-you shell; the real receipt is a PDF attachment.
+     Emits a stub record flagged needs_pdf.
+
+Usage: parse_apple.py file.eml [file2.eml ...]  -> JSON per file to stdout
+"""
+import email
+import email.policy
+import json
+import re
+import sys
+from html import unescape
+
+MONEY = r"\$([0-9][0-9,]*\.[0-9]{2})"
+CARD_BRANDS = r"(Visa|MasterCard|Mastercard|American Express|Amex|Discover|Apple Card)"
+
+
+def eml_text(path):
+    with open(path, "rb") as f:
+        msg = email.message_from_bytes(f.read(), policy=email.policy.default)
+    body = msg.get_body(preferencelist=("html", "plain"))
+    html = body.get_content() if body else ""
+    txt = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1\s*>", " ", html)
+    txt = re.sub(r"(?s)<!--.*?-->", " ", txt)
+    txt = re.sub(r"(?i)<br\s*/?>|</(td|tr|div|p|table)\s*>", "\n", txt)
+    txt = re.sub(r"<[^>]+>", " ", txt)
+    txt = unescape(txt)
+    txt = re.sub(r"[ \t ]+", " ", txt)
+    txt = re.sub(r"\s*\n\s*", "\n", txt).strip()
+    return msg, txt
+
+
+def flat(txt):
+    return re.sub(r"\s+", " ", txt)
+
+
+def money(m):
+    return float(m.replace(",", "")) if m else None
+
+
+def header_date_iso(msg):
+    try:
+        return msg["date"].datetime.date().isoformat()
+    except Exception:
+        return None
+
+
+def body_date_iso(s):
+    m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", s)
+    if not m:
+        return None
+    from datetime import datetime
+    try:
+        return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
+    except ValueError:
+        try:
+            return datetime.strptime(m.group(1), "%b %d, %Y").date().isoformat()
+        except ValueError:
+            return None
+
+
+def parse_payment(s):
+    """Find card brand + last4 in text like 'Visa .... 8761' or
+    'Visa •••• 1234 (Apple Pay)' or bare 'Apple Card'."""
+    m = re.search(CARD_BRANDS + r"[ .•·․]*([0-9]{4})?", s)
+    if not m:
+        return None
+    brand = m.group(1)
+    last4 = m.group(2)
+    ptype = "apple_card" if brand == "Apple Card" else "card"
+    if "(Apple Pay)" in s[m.start():m.end() + 15]:
+        ptype = "apple_pay"
+    return {"type": ptype, "card_brand": brand, "card_last4": last4}
+
+
+STORE_SECTIONS = ("App Store", "Apple TV", "Apple Music", "iTunes Store",
+                  "Apple Books", "iCloud+", "iCloud", "Apple Arcade",
+                  "Apple News+", "Apple Fitness+", "Apple One")
+ITEM_NOISE = re.compile(
+    r"(Write a Review \| Report a Problem|Write a Review|Report a Problem|"
+    r"Renews [A-Z][a-z]+ \d{1,2}, \d{4}|Renews [A-Z][a-z]+ \d{1,2} \d{4}|"
+    r"TYPE PURCHASED FROM PRICE|"
+    r"\(Automatic Renewal\)|Tyler[’']s [A-Za-z ]+)")
+
+
+def split_items(segment):
+    """Split a text run into items: each item is text ending in a $price."""
+    items = []
+    for m in re.finditer(r"(.*?)" + MONEY + r"(?=\s|$)", segment, re.S):
+        desc = flat(m.group(1)).strip(" |•-")
+        price = money(m.group(2))
+        desc = ITEM_NOISE.sub(" ", desc)
+        for sect in STORE_SECTIONS:
+            if desc.startswith(sect + " "):
+                desc = desc[len(sect):].strip()
+                break
+            if desc == sect:
+                desc = ""
+        desc = re.sub(r"\s+", " ", desc).strip(" |,")
+        if desc and price is not None:
+            items.append({"description": desc[:120], "quantity": 1,
+                          "unit_price": price, "total": price})
+    return items
+
+
+def parse_classic(s, out):
+    """Classic layout: Receipt APPLE ID ... TOTAL $x. The HTML contains the
+    whole receipt twice (desktop + mobile rendering); cut at the duplicate
+    boundary, then take the LAST 'TOTAL $x' in that half (2016-era emails
+    also show a TOTAL in the header block before the items)."""
+    dup = list(re.finditer(r"Receipt APPLE (ID|ACCOUNT)", s))
+    cut = dup[1].start() if len(dup) > 1 else None
+    cr = re.search(r"Copyright ©", s)
+    if cr and (cut is None or cr.start() < cut):
+        cut = cr.start()
+    first = s[:cut] if cut else s
+    totals = list(re.finditer(r"TOTAL " + MONEY, first))
+    out["grand_total"] = money(totals[-1].group(1)) if totals else None
+    m2 = re.search(r"Subtotal " + MONEY, first)
+    out["subtotal"] = money(m2.group(1)) if m2 else None
+    m2 = re.search(r"Tax " + MONEY, first)
+    out["tax"] = money(m2.group(1)) if m2 else None
+    m2 = re.search(r"ORDER ID[: ]+([A-Z0-9]+)", first)
+    out["order_id"] = m2.group(1) if m2 else None
+    m2 = re.search(r"DATE ([A-Z][a-z]+ \d{1,2}, \d{4})", first)
+    if m2:
+        out["date"] = body_date_iso(m2.group(0)) or out["date"]
+    m2 = re.search(r"BILLED TO (.{0,60})", first)
+    if m2:
+        out["payment_method"] = parse_payment(m2.group(1))
+    # items live between DOCUMENT NO. <digits> and Subtotal/TOTAL
+    m2 = re.search(r"DOCUMENT NO\.? ?(\d+)(.*?)(?:Subtotal|TOTAL)", first, re.S)
+    if m2:
+        out["items"] = split_items(m2.group(2))
+    out["merchant_name"] = "Apple (App Store)"
+    return out
+
+
+def parse_new(s, out):
+    """Late-2025+ layout: Receipt <date> Order ID: X ... Subtotal $x
+    <Card> •••• 1234 (Apple Pay) $x"""
+    m = re.search(r"Order ID:? ([A-Z0-9]+)", s)
+    out["order_id"] = m.group(1) if m else None
+    m = re.search(r"Receipt(?: & Renewal Notice)? ([A-Z][a-z]+ \d{1,2}, \d{4})", s)
+    if m:
+        out["date"] = body_date_iso(m.group(1)) or out["date"]
+    m = re.search(r"Subtotal " + MONEY, s)
+    out["subtotal"] = money(m.group(1)) if m else None
+    m = re.search(r"Tax " + MONEY, s)
+    out["tax"] = money(m.group(1)) if m else None
+    # payment line: "<brand> •••• 1234 (Apple Pay) $x" -> grand total
+    m = re.search(CARD_BRANDS + r"[ •.]*([0-9]{4})?( \(Apple Pay\))? " + MONEY, s)
+    if m:
+        out["payment_method"] = parse_payment(m.group(0))
+        out["grand_total"] = money(m.group(4))
+    if out["grand_total"] is None:
+        out["grand_total"] = out["subtotal"]
+    # items between "Apple Account: <email>" and "Billing and Payment"
+    m = re.search(r"Apple Account:? \S+@\S+(.*?)Billing and Payment", s, re.S)
+    if m:
+        seg = re.sub(r"(Agreement/Policy number: \d+|Next Billing Date: [A-Z][a-z]+ \d{1,2}, \d{4}|"
+                     r"Next Contract Renewal Date: [A-Z][a-z]+ \d{1,2}, \d{4}|"
+                     r"Renews [A-Z][a-z]+ \d{1,2}, \d{4})", " ", m.group(1))
+        out["items"] = split_items(seg)
+    out["merchant_name"] = "Apple (App Store)"
+    return out
+
+
+def parse_apple_cash(s, out):
+    out["merchant_name"] = "Apple Cash"
+    m = re.search(r"Transaction ID:? (\w+)", s)
+    out["order_id"] = m.group(1) if m else None
+    m = re.search(r"(\d{2}/\d{2}/\d{2,4}) (.*?) " + MONEY + " " + MONEY + " " + MONEY, s)
+    if m:
+        from datetime import datetime
+        for fmt in ("%m/%d/%y", "%m/%d/%Y"):
+            try:
+                out["date"] = datetime.strptime(m.group(1), fmt).date().isoformat()
+                break
+            except ValueError:
+                pass
+        desc, fee, amount, total = m.group(2), money(m.group(3)), money(m.group(4)), money(m.group(5))
+        out["items"] = [{"description": flat(desc).strip()[:120], "quantity": 1,
+                         "unit_price": amount, "total": amount}]
+        out["subtotal"] = amount
+        out["grand_total"] = total
+        out["tax"] = None
+    out["payment_method"] = {"type": "apple_cash", "card_brand": None, "card_last4": None}
+    return out
+
+
+def parse_order(s, out):
+    out["merchant_name"] = "Apple Store (online)"
+    m = re.search(r"Order Number:? (\w+)", s)
+    out["order_id"] = m.group(1) if m else None
+    m = re.search(r"Ordered on:? ([A-Z][a-z]+ \d{1,2}, \d{4})", s)
+    if m:
+        out["date"] = body_date_iso(m.group(1)) or out["date"]
+    m = re.search(r"Subtotal " + MONEY, s)
+    out["subtotal"] = money(m.group(1)) if m else None
+    m = re.search(r"(?:Estimated )?Tax " + MONEY, s)
+    out["tax"] = money(m.group(1)) if m else None
+    m = re.search(r"Order Total " + MONEY, s)
+    out["grand_total"] = money(m.group(1)) if m else None
+    # items: "<name> $price Qty n $line_total"  or  "<name> ... $price"
+    items = []
+    for m in re.finditer(r"([A-Z][^\n$]{3,90}?) " + MONEY + r" Qty (\d+) " + MONEY, flat(s)):
+        items.append({"description": m.group(1).strip()[:120],
+                      "quantity": int(m.group(3)),
+                      "unit_price": money(m.group(2)),
+                      "total": money(m.group(4))})
+    for m in re.finditer(r"(AppleCare\+[^$\n]{0,80}?)\.? " + MONEY, flat(s)):
+        items.append({"description": flat(m.group(1)).strip()[:120], "quantity": 1,
+                      "unit_price": money(m.group(2)), "total": money(m.group(2))})
+    out["items"] = items
+    m = re.search(r"Billing and Payment(.{0,400})", flat(s))
+    if m:
+        out["payment_method"] = parse_payment(m.group(1))
+    return out
+
+
+def parse_retail_stub(msg, s, out):
+    subj = msg["subject"] or ""
+    m = re.search(r"receipt from (Apple[^.]*)", subj)
+    out["merchant_name"] = m.group(1).strip() if m else "Apple Store (retail)"
+    for part in msg.walk():
+        fn = part.get_filename() or ""
+        if fn.lower().endswith(".pdf"):
+            out["notes"] = f"receipt data in PDF attachment {fn}; body has no amounts"
+            dm = re.search(r"(20\d{6})R(\d+)", fn)
+            if dm:
+                d = dm.group(1)
+                out["date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
+                out["order_id"] = "R" + dm.group(2)
+    out["needs_pdf"] = True
+    return out
+
+
+def parse(path):
+    msg, txt = eml_text(path)
+    s = flat(txt)
+    subj = (msg["subject"] or "").replace(" ", " ")
+    from_addr = str(msg["from"] or "")
+    dom_m = re.search(r"@([\w.-]+)", from_addr)
+    out = {
+        "source": "email",
+        "message_id": str(msg["message-id"] or "").strip(),
+        "sender_domain": dom_m.group(1).lower() if dom_m else None,
+        "merchant_name": "Apple",
+        "date": header_date_iso(msg),
+        "order_id": None,
+        "grand_total": None,
+        "subtotal": None,
+        "tax": None,
+        "tip": None,
+        "item_count": None,
+        "items": [],
+        "payment_method": None,
+        "currency": "USD" if "$" in s else None,
+        "raw_ref": {"mbox_file": None, "byte_offset": None},
+        "email_subject": subj,
+        "receipt_kind": None,
+    }
+
+    if re.search(r"Apple Cash Transaction Receipt", s):
+        out["receipt_kind"] = "apple_cash_payment"
+        parse_apple_cash(s, out)
+    elif re.search(r"Order Number: W\d+", s) or re.search(r"Ordered on:", s):
+        out["receipt_kind"] = "store_order"
+        parse_order(s, out)
+    elif re.search(r"receipt from Apple (?!\.)[A-Z]", subj) or (
+            "Thank you for shopping at the Apple Store" in s and len(s) < 600):
+        out["receipt_kind"] = "retail_store_pdf"
+        parse_retail_stub(msg, s, out)
+    elif re.search(r"APPLE (ID|ACCOUNT)", s) and re.search(r"ORDER ID", s):
+        out["receipt_kind"] = "app_store_classic"
+        parse_classic(s, out)
+    elif re.search(r"Receipt(?: & Renewal Notice)? [A-Z][a-z]+ \d{1,2}, \d{4} Order ID:", s):
+        out["receipt_kind"] = "app_store_new"
+        parse_new(s, out)
+    elif re.search(r"Subscription Confirmation", subj, re.I):
+        out["receipt_kind"] = "subscription_terms_notice"
+        out["notes"] = "offer-acceptance notice, no charge amount; not a receipt"
+        m = re.search(r"App (.*?) Subscription", s)
+        if m:
+            out["items"] = [{"description": flat(m.group(1)).strip()[:120],
+                             "quantity": 1, "unit_price": None, "total": None}]
+    else:
+        out["receipt_kind"] = "unknown"
+
+    out["item_count"] = len(out["items"]) if out["items"] else (None if out["receipt_kind"] in ("retail_store_pdf", "unknown") else 0)
+    return out
+
+
+if __name__ == "__main__":
+    for p in sys.argv[1:]:
+        rec = parse(p)
+        print(json.dumps(rec, indent=2, ensure_ascii=False))
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_chase_alerts.py b/infra/email_receipt_inbox/lambdas/parsers/parse_chase_alerts.py
new file mode 100644
index 000000000..dc7d32d36
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_chase_alerts.py
@@ -0,0 +1,187 @@
+#!/usr/bin/env python3
+"""Parse a Chase transaction-alert .eml into the receipts-online summary schema.
+
+IMPORTANT CONTEXT: chase.com emails are NOT receipts. They are account alerts
+(debit-card transaction over the user's $125 alert threshold, outbound
+transfers, Zelle received, external transfers, ATM withdrawals). They carry
+merchant/counterparty descriptor + amount + timestamp + account last4, but
+never items/subtotal/tax/tip. They are useful as a RECONCILIATION SIGNAL
+(match against Chase CSV rows or against real merchant receipts), not as
+receipt sources.
+
+Usage: parse_chase-alerts.py path/to/message.eml [more.eml ...]
+Emits one JSON object per file to stdout (and optionally writes alongside).
+
+If a sibling file <name>.eml.ref.json exists with {mbox_file, byte_offset},
+it is used to fill raw_ref; otherwise raw_ref points at the .eml path.
+"""
+
+import html as htmlmod
+import json
+import os
+import re
+import sys
+from datetime import datetime, timezone
+from email import policy
+from email.parser import BytesParser
+from email.utils import parsedate_to_datetime
+
+MONTHS = {m: i + 1 for i, m in enumerate(
+    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
+     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
+
+
+def load_body_text(msg):
+    """Prefer HTML (stripped) over plain text; return whitespace-normalized text."""
+    html_body, text_body = None, None
+    for part in msg.walk():
+        ct = part.get_content_type()
+        try:
+            if ct == "text/html" and html_body is None:
+                html_body = part.get_content()
+            elif ct == "text/plain" and text_body is None:
+                text_body = part.get_content()
+        except Exception:
+            continue
+    raw = html_body or text_body or ""
+    if html_body:
+        raw = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
+        raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
+        raw = re.sub(r"<[^>]+>", " ", raw)
+        raw = htmlmod.unescape(raw)
+    return re.sub(r"\s+", " ", raw).strip()
+
+
+def parse_amount(s):
+    return float(s.replace(",", ""))
+
+
+def parse_alert_datetime(text, fallback_dt):
+    """Parse 'Jun 10, 2026 at 5:47 PM ET' or '06/16/2017 9:32:16 PM EDT'."""
+    m = re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})(?: at \d{1,2}:\d{2} [AP]M)?", text)
+    if m and m.group(1) in MONTHS:
+        return "%s-%02d-%02d" % (m.group(3), MONTHS[m.group(1)], int(m.group(2)))
+    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
+    if m:
+        return "%s-%s-%s" % (m.group(3), m.group(1), m.group(2))
+    return fallback_dt.date().isoformat() if fallback_dt else None
+
+
+# Each pattern -> (alert_type, payment_type, merchant_group, amount_group, sign)
+# sign: 'debit' = money out (spend), 'credit' = money in.
+PATTERNS = [
+    # New-format (2019+) debit card alert
+    (re.compile(r"debit card transaction of \$([\d,]+\.\d{2}) with (.+?) Account ending"),
+     "debit_card_transaction", "debit_card", 2, 1, "debit"),
+    # Old-format (pre-2019) debit card alert
+    (re.compile(r"A \$([\d,]+\.\d{2}) debit card transaction to (.+?) on \d{2}/\d{2}/\d{4}"),
+     "debit_card_transaction", "debit_card", 2, 1, "debit"),
+    # Outbound transfer / bill pay ("You sent $X to RECIPIENT")
+    (re.compile(r"You sent \$([\d,]+\.\d{2}) to (.+?) Account ending"),
+     "outbound_transfer", "bank_transfer", 2, 1, "debit"),
+    # External transfer (old plaintext format)
+    (re.compile(r"A \$([\d,]+\.\d{2}) external transfer to (.+?) on "),
+     "external_transfer", "bank_transfer", 2, 1, "debit"),
+    # Zelle received, new format
+    (re.compile(r"(?:Zelle ?\S* payment )?(.+?) sent you money Here are the details: Amount \$([\d,]+\.\d{2})"),
+     "zelle_received", "zelle", 1, 2, "credit"),
+    # Zelle/QuickPay received, old format
+    (re.compile(r"(.+?) sent you money through Chase QuickPay.*?Amount: \$([\d,]+\.\d{2})"),
+     "zelle_received", "zelle", 1, 2, "credit"),
+    # ATM withdrawal
+    (re.compile(r"A \$([\d,]+\.\d{2}) ATM withdrawal on "),
+     "atm_withdrawal", "atm", None, 1, "debit"),
+]
+
+
+def parse(eml_path):
+    with open(eml_path, "rb") as f:
+        raw = f.read()
+    msg = BytesParser(policy=policy.default).parsebytes(raw)
+    text = load_body_text(msg)
+
+    hdr_dt = None
+    if msg["Date"]:
+        try:
+            hdr_dt = parsedate_to_datetime(msg["Date"])
+        except Exception:
+            hdr_dt = None
+
+    alert_type = payment_type = merchant = None
+    amount = None
+    sign = None
+    for rx, atype, ptype, mg, ag, sgn in PATTERNS:
+        m = rx.search(text)
+        if m:
+            alert_type = atype
+            payment_type = ptype
+            amount = parse_amount(m.group(ag))
+            if mg is not None:
+                merchant = re.sub(r"\s+", " ", m.group(mg)).strip()
+                # strip preview-text junk that can precede the Zelle sender name
+                merchant = re.sub(r"^.*inbox preview\.?\s*", "", merchant)
+                merchant = re.sub(r"^Zelle \S* payment\s*", "", merchant)
+            sign = sgn
+            break
+
+    # account last4: "(...1234)" / "(…1234)" / "account ending in 1234"
+    last4 = None
+    m = re.search(r"[Aa]ccount ending in [^0-9]{0,4}(\d{4})", text) or \
+        re.search(r"\(\W*(\d{4})\)", msg["Subject"] or "")
+    if m:
+        last4 = m.group(1)
+
+    date_iso = parse_alert_datetime(text, hdr_dt)
+
+    # Zelle transaction number doubles as an order/reference id
+    order_id = None
+    m = re.search(r"Transaction number (\d+)", text)
+    if m:
+        order_id = m.group(1)
+
+    out = {
+        "source": "email",
+        "message_id": (msg["Message-ID"] or "").strip() or None,
+        "sender_domain": (msg["From"] or "").split("@")[-1].rstrip(">").lower() or None,
+        "merchant_name": merchant,
+        "date": date_iso,
+        "order_id": order_id,
+        "grand_total": amount,
+        "subtotal": None,
+        "tax": None,
+        "tip": None,
+        "item_count": 0,
+        "items": [],
+        "payment_method": {"type": payment_type, "card_last4": last4},
+        "currency": "USD" if amount is not None else None,
+        "raw_ref": None,
+        # Extension fields (not in the paper-receipt schema, but essential for
+        # using these as reconciliation signals rather than receipts):
+        "alert_type": alert_type,          # debit_card_transaction | outbound_transfer | ...
+        "direction": sign,                 # debit (money out) | credit (money in)
+        "is_receipt": False,               # these are alerts, never receipts
+    }
+
+    ref_path = eml_path + ".ref.json"
+    if os.path.exists(ref_path):
+        with open(ref_path) as f:
+            ref = json.load(f)
+        out["raw_ref"] = {"mbox_file": ref.get("mbox_file"),
+                          "byte_offset": ref.get("byte_offset")}
+    else:
+        out["raw_ref"] = {"mbox_file": os.path.abspath(eml_path), "byte_offset": 0}
+    return out
+
+
+def main():
+    paths = sys.argv[1:]
+    if not paths:
+        print("usage: parse_chase-alerts.py file.eml [...]", file=sys.stderr)
+        sys.exit(2)
+    for p in paths:
+        result = parse(p)
+        print(json.dumps(result, indent=2))
+
+
+if __name__ == "__main__":
+    main()
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_costco.py b/infra/email_receipt_inbox/lambdas/parsers/parse_costco.py
new file mode 100644
index 000000000..3b329579b
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_costco.py
@@ -0,0 +1,207 @@
+#!/usr/bin/env python3
+"""Prototype parser for Costco sender-group emails (online.costco.com,
+digital.costco.com, costco.com, trx.costco.com, costco.com.mx).
+
+Reality of this group (measured on Tyler's mailbox, 2019-2026):
+  - ~99% of the 2,256 messages are marketing (Treasure Hunt, savings books).
+  - There are NO Costco.com online-order confirmations and NO US warehouse
+    e-receipts in the mailbox (not enrolled; Costco-via-Instacart orders come
+    from instacart.com and belong to that group).
+  - The only monetary receipt is the Costco Mexico gas e-ticket
+    ("Su Ticket Costo Gas") which carries a PDF of the pump receipt.
+  - Membership renewal thank-yous / auto-renew confirmations carry NO amounts;
+    they are emitted as skeleton records (nulls) so the membership charge can
+    still be date-anchored for Chase reconciliation.
+
+Usage: parse_costco.py FILE.eml [FILE2.eml ...]  -> JSON per file on stdout
+"""
+import email
+import email.policy
+import json
+import re
+import sys
+from datetime import datetime
+from html.parser import HTMLParser
+
+try:
+    from pypdf import PdfReader  # present on this machine; used for gas PDFs
+except ImportError:  # pragma: no cover
+    PdfReader = None
+
+MARKETING_PAT = re.compile(
+    r"treasure hunt|savings|hot buys|deals|warehouse insider|shop |sale|"
+    r"member-only|holiday|last chance|starts today|ends today|preview",
+    re.I,
+)
+
+
+class _TextExtractor(HTMLParser):
+    def __init__(self):
+        super().__init__()
+        self.chunks = []
+        self._skip = 0
+
+    def handle_starttag(self, tag, attrs):
+        if tag in ("style", "script"):
+            self._skip += 1
+
+    def handle_endtag(self, tag):
+        if tag in ("style", "script"):
+            self._skip = max(0, self._skip - 1)
+
+    def handle_data(self, data):
+        if not self._skip and data.strip():
+            self.chunks.append(data.strip())
+
+
+def _body_text(msg):
+    body = msg.get_body(preferencelist=("html", "plain"))
+    if body is None:
+        return ""
+    content = body.get_content()
+    if body.get_content_type() == "text/html":
+        ex = _TextExtractor()
+        ex.feed(content)
+        return "\n".join(ex.chunks)
+    return content
+
+
+def _base_record(msg, path):
+    date = None
+    if msg["Date"]:
+        try:
+            date = email.utils.parsedate_to_datetime(msg["Date"]).isoformat()
+        except (TypeError, ValueError):
+            pass
+    from_addr = email.utils.parseaddr(msg.get("From", ""))[1]
+    return {
+        "source": "email",
+        "message_id": (msg.get("Message-ID") or "").strip("<> "),
+        "sender_domain": from_addr.rsplit("@", 1)[-1].lower() if "@" in from_addr else None,
+        "merchant_name": "Costco",
+        "date": date,
+        "order_id": None,
+        "grand_total": None,
+        "subtotal": None,
+        "tax": None,
+        "tip": None,
+        "item_count": None,
+        "items": [],
+        "payment_method": {"type": None, "card_last4": None},
+        "currency": "USD",
+        "raw_ref": {"mbox_file": None, "byte_offset": None, "eml_path": path},
+    }
+
+
+def _money(s):
+    return float(s.replace(",", "").replace("$", ""))
+
+
+def _parse_gas_pdf(msg, rec):
+    """Costco Mexico gas e-ticket: full receipt lives in a PDF attachment."""
+    pdf_bytes = None
+    for part in msg.iter_attachments():
+        if part.get_content_type() == "application/pdf":
+            pdf_bytes = part.get_content()
+            break
+    if pdf_bytes is None or PdfReader is None:
+        rec["_note"] = "gas ticket PDF missing or pypdf unavailable"
+        return rec
+    import io
+    import os
+
+    reader = PdfReader(io.BytesIO(pdf_bytes))
+    text = "\n".join(p.extract_text() or "" for p in reader.pages)
+    rec["merchant_name"] = "Costco Gas"
+    rec["currency"] = "MXN"
+
+    if not text.strip():
+        # Measured case: the PDF has no text layer, just a PNG scan of the
+        # pump ticket. Extract the image and hand it to the existing paper-
+        # receipt OCR pipeline (LayoutLM) instead of parsing here.
+        img_paths = []
+        for i, page in enumerate(reader.pages):
+            for img in getattr(page, "images", []):
+                out = os.path.splitext(rec["raw_ref"]["eml_path"])[0] + f"_p{i}_{img.name}"
+                with open(out, "wb") as fh:
+                    fh.write(img.data)
+                img_paths.append(out)
+        rec["_note"] = "image-only PDF; route extracted image(s) through OCR pipeline"
+        rec["needs_ocr"] = True
+        rec["ocr_image_paths"] = img_paths
+        return rec
+
+    m = re.search(r"FOLIO\s+(\d+)", text)
+    if m:
+        rec["order_id"] = m.group(1)
+    m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}:\d{2}:\d{2})", text)
+    if m:  # DD/MM/YYYY on the printed ticket
+        dd, mm, yyyy, hms = m.groups()
+        rec["date"] = f"{yyyy}-{mm}-{dd}T{hms}"
+
+    prod = re.search(r"PRODUCTO\s*:\s*(.+)", text)
+    qty = re.search(r"CANTIDAD\s*:\s*([\d.,]+)\s*L", text)
+    price = re.search(r"PRECIO\s*:\s*([\d.,]+)", text)
+    monto = re.search(r"MONTO\s*:\s*([\d.,]+)", text)
+    if prod:
+        item = {
+            "description": prod.group(1).strip() + " (gasoline, liters)",
+            "quantity": _money(qty.group(1)) if qty else None,
+            "unit_price": _money(price.group(1)) if price else None,
+            "total": _money(monto.group(1)) if monto else None,
+        }
+        rec["items"] = [item]
+        rec["item_count"] = 1
+
+    m = re.search(r"SUBTOTAL\s*:\s*([\d.,]+)", text)
+    if m:
+        rec["subtotal"] = _money(m.group(1))
+    m = re.search(r"IVA\s*:\s*([\d.,]+)", text)
+    if m:
+        rec["tax"] = _money(m.group(1))
+    m = re.search(r"TOTAL\s*:\s*\$?\s*([\d.,]+)", text)
+    if m:
+        rec["grand_total"] = _money(m.group(1))
+    m = re.search(r"TARJETA\s*:\s*\d+\*+(\d{4})", text)
+    if m:
+        rec["payment_method"] = {"type": "card", "card_last4": m.group(1)}
+    return rec
+
+
+def parse(path):
+    with open(path, "rb") as fh:
+        msg = email.message_from_binary_file(fh, policy=email.policy.default)
+    subject = msg.get("Subject", "") or ""
+    rec = _base_record(msg, path)
+
+    # 1. Costco Mexico gas e-ticket (the only true monetary receipt).
+    if re.search(r"ticket\s+costo\s+gas", subject, re.I):
+        return _parse_gas_pdf(msg, rec)
+
+    # 2. Membership lifecycle: renewal/new-member confirmations. No amounts in
+    #    the email; emit skeleton so the charge date can anchor reconciliation.
+    if re.search(r"thank you for being a costco member|welcome to costco", subject, re.I):
+        rec["merchant_name"] = "Costco Membership"
+        rec["_note"] = "membership confirmation; email contains no amount"
+        return rec
+    if re.search(r"auto renew", subject, re.I):
+        rec["merchant_name"] = "Costco Membership"
+        rec["_note"] = "auto-renew enrollment notice; no amount, future charge"
+        return rec
+
+    # 3. Known non-receipt transactional notices.
+    if re.search(r"card transfer confirmation|password|account has been verified|"
+                 r"recall notice|e-waste", subject, re.I):
+        return {"skip": True, "reason": "transactional-non-receipt", "subject": subject}
+
+    # 4. Marketing.
+    body = _body_text(msg)[:4000]
+    if MARKETING_PAT.search(subject) or MARKETING_PAT.search(body):
+        return {"skip": True, "reason": "marketing", "subject": subject}
+
+    return {"skip": True, "reason": "unrecognized", "subject": subject}
+
+
+if __name__ == "__main__":
+    for p in sys.argv[1:]:
+        print(json.dumps(parse(p), indent=2, ensure_ascii=False))
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_doordash.py b/infra/email_receipt_inbox/lambdas/parsers/parse_doordash.py
new file mode 100644
index 000000000..5dacc5855
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_doordash.py
@@ -0,0 +1,284 @@
+#!/usr/bin/env python3
+"""Prototype parser for DoorDash receipt emails (no-reply@doordash.com).
+
+Usage: parse_doordash.py FILE.eml [--mbox-file NAME --byte-offset N]
+
+Emits JSON on stdout matching the receipts-online email schema:
+{ source, message_id, sender_domain, merchant_name, date, order_id,
+  grand_total, subtotal, tax, tip, item_count, items[], payment_method,
+  currency, raw_ref }
+
+Format eras observed (2017-2026):
+  A) 2018-2019  "Order Confirmation": items + per-item prices, merchant+total
+     inline ("<Merchant> $22.39 Paid with Apple Pay"); NO subtotal/tax/tip.
+  B) 2020-2022  "Order Confirmation"/"we got your order": full breakdown
+     (items, Subtotal, Taxes, Delivery Fee, Service Fee, Tip, Discounts,
+     Total Charged). Richest era.
+  C) 2023-2026  "Order Confirmation": itemization REMOVED. Only merchant,
+     "Estimated Total"/"Total Charged" and payment type. Items live behind
+     a "View Full Receipt" web link.
+  D) 2024-2026  "Final receipt" (grocery/convenience only): full itemization
+     incl. substitutions, Subtotal, Tax, fees, Dasher tip, Final total charged.
+"""
+import json
+import re
+import sys
+from email import policy
+from email.parser import BytesParser
+from email.utils import parsedate_to_datetime
+from html.parser import HTMLParser
+
+MONEY = r"-?\$([\d,]+\.\d{2})"
+
+
+class _TextExtractor(HTMLParser):
+    """Flatten HTML to text lines, one per block-ish element."""
+
+    BLOCK = {"tr", "br", "p", "div", "td", "li", "h1", "h2", "h3", "table"}
+
+    def __init__(self):
+        super().__init__(convert_charrefs=True)
+        self.chunks = []
+        self._skip = 0
+
+    def handle_starttag(self, tag, attrs):
+        if tag in ("style", "script"):
+            self._skip += 1
+        if tag in self.BLOCK:
+            self.chunks.append("\n")
+
+    def handle_endtag(self, tag):
+        if tag in ("style", "script"):
+            self._skip = max(0, self._skip - 1)
+        if tag in self.BLOCK:
+            self.chunks.append("\n")
+
+    def handle_data(self, data):
+        if not self._skip:
+            self.chunks.append(data)
+
+
+def html_to_lines(html):
+    p = _TextExtractor()
+    p.feed(html)
+    text = "".join(p.chunks)
+    # kill zero-width / soft-hyphen / nbsp preheader junk
+    text = re.sub(r"[​‌‍­͏﻿]", "", text)
+    text = text.replace("\xa0", " ")
+    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
+    return [ln for ln in lines if ln]
+
+
+def money(s):
+    m = re.search(MONEY, s)
+    if not m:
+        return None
+    v = float(m.group(1).replace(",", ""))
+    return -v if s.lstrip().startswith("-") else v
+
+
+def find_labeled_amount(lines, labels):
+    """Label on one line, $amount on same or next line."""
+    for i, ln in enumerate(lines):
+        low = ln.lower()
+        for lab in labels:
+            if low == lab or low.startswith(lab):
+                v = money(ln)
+                if v is None and i + 1 < len(lines):
+                    v = money(lines[i + 1])
+                if v is not None:
+                    return v
+    return None
+
+
+def parse_items(lines):
+    """Item blocks: '<N>x' (qty alone or with description), description /
+    bullet-modifier lines, then a $price line.  Substitution sections in
+    'Final receipt' emails list the original under 'Substituted' and the
+    replacement under 'Substituted with:'; only the replacement is kept."""
+    items = []
+    i = 0
+    in_adjusted = False
+    take_next_sub = True  # within adjusted section: original vs replacement
+    end_markers = ("subtotal", "estimated total", "total charged", "final total")
+    while i < len(lines):
+        ln = lines[i]
+        low = ln.lower()
+        if low.startswith("items that were adjusted"):
+            in_adjusted = True
+        elif low.startswith("items you ordered"):
+            in_adjusted = False
+        elif low.startswith("substituted with"):
+            take_next_sub = True
+        elif low == "substituted" or low.startswith("substituted ("):
+            take_next_sub = False
+        elif any(low.startswith(m) for m in end_markers) and money(ln) is None:
+            # breakdown section begins; stop item scan only for the totals
+            pass
+        m = re.match(r"^(\d+)\s*x\b\s*(.*)$", ln, re.I)
+        if m:
+            qty = int(m.group(1))
+            desc = m.group(2).strip()
+            price = money(desc)
+            if price is not None:
+                desc = re.sub(MONEY, "", desc).strip()
+            j = i + 1
+            # gather description / modifiers until a price line
+            while j < len(lines) and price is None and j - i < 15:
+                nxt = lines[j]
+                if money(nxt) is not None and re.fullmatch(r"-?\$[\d,]+\.\d{2}", nxt):
+                    price = money(nxt)
+                    j += 1
+                    break
+                if nxt.startswith(("•", "-", "*")):
+                    j += 1
+                    continue
+                if re.match(r"^(\d+)\s*x\b", nxt) or nxt.lower().startswith(
+                    ("subtotal", "estimated total", "total charged")
+                ):
+                    break
+                if not desc:
+                    desc = nxt
+                j += 1
+            if desc and price is not None:
+                keep = take_next_sub if in_adjusted else True
+                if keep:
+                    items.append(
+                        {
+                            "description": desc,
+                            "quantity": qty,
+                            "unit_price": round(price / qty, 2) if qty else None,
+                            "total": price,
+                        }
+                    )
+            i = j
+            continue
+        i += 1
+    return items
+
+
+def parse_eml(path, mbox_file=None, byte_offset=None):
+    with open(path, "rb") as f:
+        msg = BytesParser(policy=policy.default).parse(f)
+
+    subject = msg.get("Subject", "") or ""
+    body = msg.get_body(preferencelist=("html", "plain"))
+    content = body.get_content() if body else ""
+    if body is not None and body.get_content_type() == "text/html":
+        lines = html_to_lines(content)
+    else:
+        lines = [re.sub(r"\s+", " ", ln).strip() for ln in content.split("\n")]
+        lines = [ln for ln in lines if ln]
+
+    # --- merchant ---
+    merchant = None
+    m = re.search(
+        r"(?:Order Confirmation for .+? from|Final receipt for .+? from|"
+        r"Details of your .*?delivery from)\s+(.+)$",
+        subject,
+    )
+    if m:
+        merchant = m.group(1).strip().rstrip(".")
+    if not merchant:
+        for i, ln in enumerate(lines):
+            if re.match(r"^Total: \$[\d,]+\.\d{2}$", ln) and i > 0:
+                merchant = lines[i - 1]
+                break
+    if not merchant:  # era A: merchant line, then "$22.39 Paid with ..." line
+        for i, ln in enumerate(lines):
+            m = re.match(r"^(?:(.+?) )?\$[\d,]+\.\d{2} Paid with", ln)
+            if m:
+                merchant = (m.group(1) or (lines[i - 1] if i > 0 else "")).strip() or None
+                break
+
+    # --- totals ---
+    grand_total = find_labeled_amount(
+        lines, ["final total charged", "total charged", "estimated total"]
+    )
+    if grand_total is None:
+        for ln in lines:
+            m = re.match(r"^Total: (\$[\d,]+\.\d{2})$", ln)
+            if m:
+                grand_total = money(m.group(1))
+                break
+    if grand_total is None:  # mid-2022: "Total:" label, amount on next line
+        grand_total = find_labeled_amount(lines, ["total:", "total"])
+    if grand_total is None:  # era A: "[<Merchant> ]$22.39 Paid with ..."
+        for ln in lines:
+            m = re.match(r"^(?:.+? )?(\$[\d,]+\.\d{2}) Paid with", ln)
+            if m:
+                grand_total = money(m.group(1))
+                break
+
+    subtotal = find_labeled_amount(lines, ["subtotal"])
+    tax = find_labeled_amount(lines, ["taxes", "tax"])
+    tip = find_labeled_amount(lines, ["dasher tip", "tip"])
+
+    # --- items ---
+    items = parse_items(lines)
+
+    # --- payment ---
+    pay_type = None
+    full = " ".join(lines)
+    for ln in lines:
+        mm = re.search(r"Paid with\s+(.{2,50})", ln)
+        if mm:
+            pay_type = mm.group(1)
+            pay_type = re.sub(r"\s*and/or\s*credits.*$", "", pay_type)
+            pay_type = re.sub(r"\s*\$[\d,]+\.\d{2}.*$", "", pay_type)
+            pay_type = re.sub(r"\s*-\s*For:.*$", "", pay_type).strip()
+            # era A puts "<Merchant> $X Paid with Apple Pay - For: ..." on one line
+            break
+    last4 = None
+    m = re.search(r"(?:ending in|\*{2,}|x{4})\s*(\d{4})", full, re.I)
+    if m:
+        last4 = m.group(1)
+
+    # --- order id (only present in some eras' deep links) ---
+    order_id = None
+    m = re.search(r"orders?/([0-9a-f]{8}-[0-9a-f-]{27,})", content)
+    if m:
+        order_id = m.group(1)
+
+    # --- date ---
+    date_iso = None
+    try:
+        date_iso = parsedate_to_datetime(msg["Date"]).isoformat()
+    except Exception:
+        pass
+
+    return {
+        "source": "email",
+        "message_id": msg.get("Message-ID", "").strip() or None,
+        "sender_domain": (msg.get("From", "").split("@")[-1].strip("> ") or None),
+        "merchant_name": merchant,
+        "date": date_iso,
+        "order_id": order_id,
+        "grand_total": grand_total,
+        "subtotal": subtotal,
+        "tax": tax,
+        "tip": tip,
+        "item_count": len(items) if items else None,
+        "items": items,
+        "payment_method": {"type": pay_type, "card_last4": last4},
+        "currency": "USD",
+        "raw_ref": {"mbox_file": mbox_file, "byte_offset": byte_offset},
+    }
+
+
+def main():
+    args = sys.argv[1:]
+    if not args:
+        print(__doc__)
+        sys.exit(1)
+    path = args[0]
+    mbox_file = byte_offset = None
+    if "--mbox-file" in args:
+        mbox_file = args[args.index("--mbox-file") + 1]
+    if "--byte-offset" in args:
+        byte_offset = int(args[args.index("--byte-offset") + 1])
+    print(json.dumps(parse_eml(path, mbox_file, byte_offset), indent=2, ensure_ascii=False))
+
+
+if __name__ == "__main__":
+    main()
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_equinox.py b/infra/email_receipt_inbox/lambdas/parsers/parse_equinox.py
new file mode 100644
index 000000000..c4fb07cbd
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_equinox.py
@@ -0,0 +1,188 @@
+#!/usr/bin/env python3
+"""Prototype parser for Equinox receipt emails (equinox.com).
+
+Handles:
+  A. Personal Training Purchase Confirmation (noreply@equinox.com):
+     "ORDER DETAILS Order Number: N Purchase Description: X
+      Order/Billing Total: 220.00 Payment Details: MASTERCARD8644"
+     No subtotal/tax breakdown; total has no '$'. merchant "Equinox".
+  B. The Shop at Equinox order confirmation (concierge@equinox.com):
+     "Order #EQXSHOP37579 ... Order summary <item> × N \n $price ...
+      Subtotal $x Shipping $x Taxes $x Total $x USD".
+     merchant "Equinox (The Shop)".
+
+Everything else on equinox.com (session cancel/reschedule/confirm, membership
+notes, shipping notices, marketing) is not a receipt -> grand_total=None.
+
+Usage: parse_equinox.py file.eml [file2.eml ...]  -> JSON per file to stdout
+"""
+import email
+import email.policy
+import json
+import re
+import sys
+from datetime import datetime
+from html import unescape
+
+MONEY = r"\$?([0-9][0-9,]*\.[0-9]{2})"
+# brand glued to last4, e.g. MASTERCARD8644, MasterCard5454, VISA1234
+CARD_GLUED = re.compile(
+    r"(VISA|MASTERCARD|AMEX|AMERICAN EXPRESS|DISCOVER|DINERS|JCB)\s*([0-9]{4})",
+    re.I)
+BRAND_NORM = {
+    "visa": "Visa", "mastercard": "MasterCard", "amex": "American Express",
+    "american express": "American Express", "discover": "Discover",
+    "diners": "Diners Club", "jcb": "JCB",
+}
+
+
+def render(part):
+    if part is None:
+        return ""
+    html = part.get_content()
+    if part.get_content_type() == "text/plain":
+        txt = html
+    else:
+        txt = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1\s*>", " ", html)
+        txt = re.sub(r"(?s)<!--.*?-->", " ", txt)
+        txt = re.sub(r"(?i)<br\s*/?>|</(td|tr|div|p|table|li)\s*>", "\n", txt)
+        txt = re.sub(r"<[^>]+>", " ", txt)
+        txt = unescape(txt)
+    txt = re.sub(r"[ \t\xa0]+", " ", txt)
+    txt = re.sub(r"\s*\n\s*", "\n", txt).strip()
+    return txt
+
+
+def eml_text(path):
+    """Return (msg, text). Equinox PT emails are HTML-only (empty plain part);
+    Shop emails carry a good plain part. Pick whichever renders more content."""
+    with open(path, "rb") as f:
+        msg = email.message_from_bytes(f.read(), policy=email.policy.default)
+    plain = render(msg.get_body(preferencelist=("plain",)))
+    htmlt = render(msg.get_body(preferencelist=("html",)))
+    txt = plain if len(plain) >= len(htmlt) else htmlt
+    return msg, txt
+
+
+def flat(txt):
+    return re.sub(r"\s+", " ", txt)
+
+
+def money(m):
+    return float(m.replace(",", "")) if m else None
+
+
+def header_date_iso(msg):
+    try:
+        return msg["date"].datetime.date().isoformat()
+    except Exception:
+        return None
+
+
+def parse_payment(s):
+    m = CARD_GLUED.search(s)
+    if not m:
+        return None
+    brand = BRAND_NORM.get(m.group(1).lower(), m.group(1).title())
+    return {"type": "card", "card_brand": brand, "card_last4": m.group(2)}
+
+
+def parse_pt(s, out):
+    """Personal Training purchase confirmation."""
+    out["merchant_name"] = "Equinox"
+    out["receipt_kind"] = "personal_training"
+    m = re.search(r"Order Number:?\s*([A-Za-z0-9]+)", s)
+    out["order_id"] = m.group(1) if m else None
+    m = re.search(r"Purchase Description:?\s*(.+?)\s*(?:Order/Billing Total|Payment Details|$)", s)
+    desc = flat(m.group(1)).strip() if m else None
+    m = re.search(r"Order/Billing Total:?\s*" + MONEY, s)
+    total = money(m.group(1)) if m else None
+    out["grand_total"] = total
+    # PT receipts carry no subtotal/tax breakdown.
+    out["subtotal"] = total
+    out["tax"] = None
+    m = re.search(r"Payment Details:?\s*(.+)", s)
+    if m:
+        out["payment_method"] = parse_payment(m.group(1))
+    if desc and total is not None:
+        out["items"] = [{"description": desc[:120], "quantity": 1,
+                         "unit_price": total, "total": total}]
+    out["currency"] = "USD"
+    return out
+
+
+def parse_shop(txt, s, out):
+    """The Shop at Equinox order confirmation. Totals read from flattened `s`;
+    line items read from `txt` (newlines separate '<name> × N' from '$price')."""
+    out["merchant_name"] = "Equinox (The Shop)"
+    out["receipt_kind"] = "shop_order"
+    m = re.search(r"Order #\s*([A-Z0-9]+)", s)
+    out["order_id"] = m.group(1) if m else None
+    m = re.search(r"Subtotal\s*\n?\s*" + MONEY, s)
+    out["subtotal"] = money(m.group(1)) if m else None
+    m = re.search(r"Tax(?:es)?\s*\n?\s*" + MONEY, s)
+    out["tax"] = money(m.group(1)) if m else None
+    m = re.search(r"Total\s*\n?\s*" + MONEY + r"\s*USD", s)
+    if not m:
+        m = re.search(r"\bTotal\s*\n?\s*" + MONEY, s)
+    out["grand_total"] = money(m.group(1)) if m else None
+    # items: "<NAME> × N" on one line, "$price" on the next.
+    items = []
+    for im in re.finditer(r"([^\n]+?)\s*[×xX]\s*(\d+)\s*\n\s*" + MONEY, txt):
+        name = flat(im.group(1)).strip(" -|")
+        qty = int(im.group(2))
+        line_total = money(im.group(3))
+        unit = round(line_total / qty, 2) if qty else line_total
+        items.append({"description": name[:120], "quantity": qty,
+                      "unit_price": unit, "total": line_total})
+    out["items"] = items
+    out["currency"] = "USD"
+    return out
+
+
+def parse(path):
+    msg, txt = eml_text(path)
+    s = flat(txt)
+    subj = (msg["subject"] or "").strip()
+    from_addr = str(msg["from"] or "")
+    dom_m = re.search(r"@([\w.-]+)", from_addr)
+    out = {
+        "source": "email",
+        "message_id": str(msg["message-id"] or "").strip(),
+        "sender_domain": dom_m.group(1).lower() if dom_m else None,
+        "merchant_name": "Equinox",
+        "date": header_date_iso(msg),
+        "order_id": None,
+        "grand_total": None,
+        "subtotal": None,
+        "tax": None,
+        "tip": None,
+        "item_count": None,
+        "items": [],
+        "payment_method": None,
+        "currency": "USD" if "$" in s else None,
+        "raw_ref": {"mbox_file": None, "byte_offset": None},
+        "email_subject": subj,
+        "receipt_kind": None,
+    }
+
+    is_reply = re.match(r"(?i)^\s*(re|fw|fwd):", subj)
+    if not is_reply and re.search(r"Order/Billing Total|PERSONAL TRAINING\s*PURCHASE CONFIRMATION",
+                                  txt, re.I) and re.search(r"Order Number", s, re.I):
+        parse_pt(s, out)
+    elif not is_reply and re.search(r"Order #\s*EQXSHOP", s) and re.search(r"Order summary|Subtotal", s, re.I):
+        parse_shop(txt, s, out)
+    else:
+        out["receipt_kind"] = "non_receipt"
+
+    out["item_count"] = len(out["items"]) if out["receipt_kind"] not in (None, "non_receipt") else None
+    return out
+
+
+def main():
+    for p in sys.argv[1:]:
+        print(json.dumps(parse(p), indent=2, ensure_ascii=False))
+
+
+if __name__ == "__main__":
+    main()
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_github.py b/infra/email_receipt_inbox/lambdas/parsers/parse_github.py
new file mode 100644
index 000000000..ff65fc059
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_github.py
@@ -0,0 +1,189 @@
+#!/usr/bin/env python3
+"""Prototype parser for GitHub receipt emails (github.com, email.github.com).
+
+CRITICAL: github.com is overwhelmingly notification traffic (PR/issue threads,
+security alerts, billing *alerts*). Only "[GitHub] Payment Receipt ..." emails
+are actual receipts with a charged total; everything else returns
+grand_total=None (receipt_kind "non_receipt"). Note in particular that Tyler's
+own repo is a receipt platform, so thousands of PR-notification subjects contain
+the word "receipt" -- subject keywords alone are NOT sufficient.
+
+Payment receipt layout (plain text, stable 2021-2026):
+    GITHUB RECEIPT - PERSONAL SUBSCRIPTION - exampleuser
+    GitHub Copilot Pro - year: $100.00 USD      <- item (may omit price)
+    Feb 21, 2026 - Feb 20, 2027                  <- service period
+    Tax: $0.00 USD                               <- (absent pre-2023)
+    Total: $100.00 USD*
+    Charged to: Visa (4*** **** **** 1234)
+    Transaction ID: ch_...
+    Date: 22 Feb 2026 11:28AM PST
+
+merchant "GitHub".
+
+Usage: parse_github.py file.eml [file2.eml ...]  -> JSON per file to stdout
+"""
+import email
+import email.policy
+import json
+import re
+import sys
+from datetime import datetime
+from html import unescape
+
+MONEY = r"\$([0-9][0-9,]*\.[0-9]{2})"
+CARD_BRANDS = r"(Visa|MasterCard|Mastercard|American Express|Amex|Discover|PayPal)"
+
+
+def render(part):
+    if part is None:
+        return ""
+    html = part.get_content()
+    if part.get_content_type() == "text/plain":
+        txt = html
+    else:
+        txt = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1\s*>", " ", html)
+        txt = re.sub(r"(?s)<!--.*?-->", " ", txt)
+        txt = re.sub(r"(?i)<br\s*/?>|</(td|tr|div|p|table|li)\s*>", "\n", txt)
+        txt = re.sub(r"<[^>]+>", " ", txt)
+        txt = unescape(txt)
+    txt = re.sub(r"[ \t\xa0]+", " ", txt)
+    txt = re.sub(r"\s*\n\s*", "\n", txt).strip()
+    return txt
+
+
+def eml_text(path):
+    """GitHub receipts are text/plain; prefer it, fall back to html."""
+    with open(path, "rb") as f:
+        msg = email.message_from_bytes(f.read(), policy=email.policy.default)
+    plain = render(msg.get_body(preferencelist=("plain",)))
+    htmlt = render(msg.get_body(preferencelist=("html",)))
+    txt = plain if len(plain) >= len(htmlt) else htmlt
+    return msg, txt
+
+
+def flat(txt):
+    return re.sub(r"\s+", " ", txt)
+
+
+def money(m):
+    return float(m.replace(",", "")) if m else None
+
+
+def header_date_iso(msg):
+    try:
+        return msg["date"].datetime.date().isoformat()
+    except Exception:
+        return None
+
+
+def parse_payment(s):
+    """'Charged to: Visa (4*** **** **** 1234)' -> brand + last4."""
+    m = re.search(CARD_BRANDS + r"\s*\(?[0-9* ]*?([0-9]{4})\)?", s)
+    if not m:
+        m = re.search(CARD_BRANDS, s)
+        if not m:
+            return None
+        brand = m.group(1)
+        return {"type": "paypal" if brand == "PayPal" else "card",
+                "card_brand": brand, "card_last4": None}
+    brand = m.group(1).replace("Mastercard", "MasterCard")
+    return {"type": "paypal" if brand == "PayPal" else "card",
+            "card_brand": brand, "card_last4": m.group(2)}
+
+
+def parse_receipt(txt, s, out):
+    out["receipt_kind"] = "payment_receipt"
+    m = re.search(r"Transaction ID:?\s*(\S+)", s)
+    out["order_id"] = m.group(1) if m else None
+    m = re.search(r"Tax:?\s*" + MONEY, s)
+    out["tax"] = money(m.group(1)) if m else None
+    m = re.search(r"Total:?\s*" + MONEY, s)
+    total = money(m.group(1)) if m else None
+    out["grand_total"] = total
+    m = re.search(r"Charged to:?\s*(.+)", s)
+    if m:
+        out["payment_method"] = parse_payment(m.group(1))
+    # body 'Date: 22 Feb 2026 11:28AM PST' is the charge date (more precise than
+    # the mail header, which can differ by a day across timezones).
+    m = re.search(r"\bDate:?\s*(\d{1,2} [A-Za-z]{3,9} \d{4})", s)
+    if m:
+        for fmt in ("%d %b %Y", "%d %B %Y"):
+            try:
+                out["date"] = datetime.strptime(m.group(1), fmt).date().isoformat()
+                break
+            except ValueError:
+                pass
+    # items: lines in the plain-text body between the RECEIPT header and Tax/Total.
+    items = []
+    seg = re.search(r"GITHUB RECEIPT[^\n]*\n(.*?)\n(?:Tax:|Total:)", txt, re.S | re.I)
+    if seg:
+        for line in seg.group(1).splitlines():
+            line = line.strip()
+            if not line:
+                continue
+            # skip pure service-period / date-range lines
+            if re.match(r"^[A-Za-z]{3,9} \d{1,2}, \d{4}\s*-", line):
+                continue
+            if re.match(r"^For service through", line, re.I):
+                continue
+            pm = re.search(r"(.*?):?\s*" + MONEY + r"\s*USD\s*$", line)
+            if pm and pm.group(1).strip():
+                price = money(pm.group(2))
+                items.append({"description": pm.group(1).strip()[:120],
+                              "quantity": 1, "unit_price": price, "total": price})
+            else:
+                # item with no explicit price (older 'Pro yearly' / 'data packs')
+                items.append({"description": line[:120], "quantity": 1,
+                              "unit_price": None, "total": None})
+    out["items"] = items
+    if total is not None and out["tax"] is not None:
+        out["subtotal"] = round(total - out["tax"], 2)
+    out["currency"] = "USD"
+    return out
+
+
+def parse(path):
+    msg, txt = eml_text(path)
+    s = flat(txt)
+    subj = (msg["subject"] or "").strip()
+    from_addr = str(msg["from"] or "")
+    dom_m = re.search(r"@([\w.-]+)", from_addr)
+    out = {
+        "source": "email",
+        "message_id": str(msg["message-id"] or "").strip(),
+        "sender_domain": dom_m.group(1).lower() if dom_m else None,
+        "merchant_name": "GitHub",
+        "date": header_date_iso(msg),
+        "order_id": None,
+        "grand_total": None,
+        "subtotal": None,
+        "tax": None,
+        "tip": None,
+        "item_count": None,
+        "items": [],
+        "payment_method": None,
+        "currency": "USD" if "$" in s else None,
+        "raw_ref": {"mbox_file": None, "byte_offset": None},
+        "email_subject": subj,
+        "receipt_kind": None,
+    }
+
+    # A real receipt requires the plain-text 'GITHUB RECEIPT' block AND a Total.
+    # Billing *alerts* ("Annual Billing Alert", "problem billing") and all
+    # notification threads carry no total -> non_receipt.
+    if re.search(r"GITHUB RECEIPT\b", txt, re.I) and re.search(r"\bTotal:?\s*" + MONEY, s):
+        parse_receipt(txt, s, out)
+    else:
+        out["receipt_kind"] = "non_receipt"
+
+    out["item_count"] = len(out["items"]) if out["receipt_kind"] == "payment_receipt" else None
+    return out
+
+
+def main():
+    for p in sys.argv[1:]:
+        print(json.dumps(parse(p), indent=2, ensure_ascii=False))
+
+
+if __name__ == "__main__":
+    main()
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_paypal.py b/infra/email_receipt_inbox/lambdas/parsers/parse_paypal.py
new file mode 100644
index 000000000..ad6475ed6
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_paypal.py
@@ -0,0 +1,335 @@
+#!/usr/bin/env python3
+"""Prototype parser: PayPal payment-receipt emails -> receipts-online summary JSON.
+
+Usage: python3 parse_paypal.py <path.eml> [more.eml ...]
+Emits one JSON object per input (list if multiple) on stdout.
+
+Handles PayPal receipt formats 2013-2026:
+  A. 2013-2017 classic  "Receipt for Your Payment to X" (multipart text+html)
+  B. 2018-2020 classic  same layout, html-only, item table Description/Unit price/Qty/Amount
+  C. 2020-2025          "You have authorized a payment to X" (pre-auth receipt)
+  D. 2024               "Receipt for your PayPal payment" (goods & services)
+  E. 2023-2024          "You sent a $X USD payment" (friends & family, no items)
+  F. 2025-2026          "Merchant: $X USD" / "You paid $X USD to Merchant" (new template)
+
+Notes:
+  - card_last4 is usually a BANK ACCOUNT last4 (e.g. Chase ••1234) or "PayPal Balance";
+    still useful for Chase-CSV reconciliation.
+  - tax/tip almost never present on PayPal receipts (merchant-side detail).
+  - 2025+ template truncates long merchant names ("Private Internet Acc...").
+"""
+import json
+import os
+import re
+import sys
+from datetime import datetime
+from email import policy
+from email.parser import BytesParser
+from html.parser import HTMLParser
+
+
+# ---------------------------------------------------------------- html -> lines
+class _TextExtractor(HTMLParser):
+    def __init__(self):
+        super().__init__(convert_charrefs=True)
+        self.out = []
+        self.skip = 0
+
+    def handle_starttag(self, tag, attrs):
+        if tag in ("style", "script", "head", "title"):
+            self.skip += 1
+        elif tag in ("br", "tr", "p", "div", "li", "table"):
+            self.out.append("\n")
+        elif tag == "td":
+            self.out.append(" | ")
+
+    def handle_endtag(self, tag):
+        if tag in ("style", "script", "head", "title"):
+            self.skip = max(0, self.skip - 1)
+
+    def handle_data(self, data):
+        if not self.skip:
+            self.out.append(data)
+
+
+def html_to_lines(html):
+    p = _TextExtractor()
+    p.feed(html)
+    text = "".join(p.out)
+    text = text.replace("\xa0", " ").replace("‌", "").replace("’", "'")
+    lines = []
+    for raw in text.split("\n"):
+        line = re.sub(r"[ \t]+", " ", raw).strip()
+        line = re.sub(r"^(\| ?)+", "", line)          # leading empty cells
+        line = re.sub(r"( ?\| ?)+", " | ", line)       # collapse cell runs
+        line = line.strip(" |").strip()
+        if line:
+            lines.append(line)
+    return lines
+
+
+AMT = r"\$\s?([\d,]+\.\d{2}|[\d,]+)"
+
+
+def _money(s):
+    try:
+        return round(float(s.replace(",", "")), 2)
+    except (ValueError, AttributeError):
+        return None
+
+
+def _parse_date(s):
+    s = s.strip()
+    fmts = ("%b %d, %Y %H:%M:%S", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%b. %d, %Y")
+    core = re.sub(r"\s+(P[SD]T|E[SD]T|C[SD]T|M[SD]T|GMT[+\-]?\d*|UTC)$", "", s)
+    for f in fmts:
+        try:
+            return datetime.strptime(core, f).date().isoformat()
+        except ValueError:
+            continue
+    return None
+
+
+# ---------------------------------------------------------------- field pulls
+def _find_merchant(subject, full, lines):
+    subject = (subject or "").replace("\xa0", " ")
+    m = re.match(r"Receipt for [Yy]our (?:PayPal )?[Pp]ayments? to (.+)", subject)
+    if m:
+        return m.group(1).strip()
+    m = re.match(r"You have authorized a payment to (.+)", subject)
+    if m:
+        return m.group(1).strip()
+    m = re.match(r"(.+?): \$[\d,\.]+ USD\s*$", subject)
+    if m:
+        return m.group(1).strip()
+    pats = [
+        r"You (?:sent a payment|authorized a payment) (?:of|for) \$[\d,\.]+ ?USD to ([^\n(]+)",
+        r"You paid \$[\d,\.]+ ?USD to ([^\n(]+)",
+        r"You sent \$[\d,\.]+ ?USD to ([^\n(]+?)\.?\n",
+        r"You authorized a transaction to ([^\n.]+)\.",
+    ]
+    for p in pats:
+        m = re.search(p, full)
+        if m:
+            return m.group(1).strip().rstrip(".")
+    # "Merchant" block: name on next line
+    for i, ln in enumerate(lines):
+        if ln == "Merchant" and i + 1 < len(lines):
+            return lines[i + 1].split(" | ")[0].strip()
+    # goods&services P2P: "Payment to" / "RECIPIENT"
+    for i, ln in enumerate(lines):
+        m = re.search(r"(?:Payment to|RECIPIENT)$", ln) or re.search(r"\| (?:Payment to|RECIPIENT)$", ln)
+        if m and i + 1 < len(lines):
+            return lines[i + 1].split(" | ")[0].strip()
+        m2 = re.search(r"(?:Payment to|RECIPIENT)\n", ln)
+    return None
+
+
+def _find_date(full, lines, msg):
+    m = re.search(r"Transaction date\n([^\n|]+)", full)
+    if m:
+        d = _parse_date(m.group(1))
+        if d:
+            return d
+    m = re.search(r"(?:^|\n)Date\n([A-Z][a-z]+ \d{1,2}, \d{4})", full)
+    if m:
+        d = _parse_date(m.group(1))
+        if d:
+            return d
+    m = re.search(r"([A-Z][a-z]{2} \d{1,2}, \d{4} \d{2}:\d{2}:\d{2} [A-Z]{3,4})", full)
+    if m:
+        d = _parse_date(m.group(1))
+        if d:
+            return d
+    if msg["date"]:
+        try:
+            return msg["date"].datetime.date().isoformat()
+        except Exception:
+            pass
+    return None
+
+
+def _find_amount_after(label_re, full):
+    m = re.search(label_re + r"[^\n]*?\|\s*" + AMT, full)
+    if not m:
+        m = re.search(label_re + r"[^\$\n]*\n?\s*" + AMT, full)
+    return _money(m.group(1)) if m else None
+
+
+_HEADER_WORDS = {"description", "unit price", "qty", "amount", "subtotal", "total",
+                 "payment", "shipping and handling", "merchant"}
+
+
+def _clean_desc(s):
+    s = (s or "").split(" | ")[-1].strip()
+    if not s or s.startswith("$") or "Qty:" in s or s.lower() in _HEADER_WORDS:
+        return None
+    return s
+
+
+def _find_items(lines):
+    items = []
+    # S1: one line "desc | $unit [USD] | qty | $amt [USD]"
+    s1 = re.compile(
+        r"^(?P<desc>.*?)\s*\|\s*\$(?P<unit>[\d,]+\.\d{2})(?: USD)?\s*\|\s*(?P<qty>\d+)\s*\|\s*\$(?P<amt>[\d,]+\.\d{2})(?: USD)?$"
+    )
+    # S2: line "$unit USD | qty | $amt USD" (desc on the previous line, or empty)
+    s2 = re.compile(
+        r"^\$(?P<unit>[\d,]+\.\d{2})(?: USD)?\s*\|\s*(?P<qty>\d+)\s*\|\s*\$(?P<amt>[\d,]+\.\d{2})(?: USD)?$"
+    )
+    for i, ln in enumerate(lines):
+        m = s1.match(ln)
+        desc = None
+        if m:
+            desc = _clean_desc(m.group("desc"))
+        else:
+            m = s2.match(ln)
+            if m and i > 0:
+                desc = _clean_desc(lines[i - 1])
+        if m:
+            items.append({
+                "description": desc,
+                "quantity": int(m.group("qty")),
+                "unit_price": _money(m.group("unit")),
+                "total": _money(m.group("amt")),
+            })
+    if items:
+        return items
+    # S3: cell-per-line "$unit USD" / "qty" / "$amt USD" triplets
+    for i in range(len(lines) - 2):
+        if (re.fullmatch(r"\$[\d,]+\.\d{2} USD", lines[i])
+                and re.fullmatch(r"\d{1,3}", lines[i + 1])
+                and re.fullmatch(r"\$[\d,]+\.\d{2} USD", lines[i + 2])):
+            items.append({
+                "description": _clean_desc(lines[i - 1]) if i > 0 else None,
+                "quantity": int(lines[i + 1]),
+                "unit_price": _money(lines[i].strip("$ USD").replace("USD", "")),
+                "total": _money(lines[i + 2].replace("USD", "").strip("$ ")),
+            })
+    if items:
+        return items
+    # S4 (2025+): "desc" / "Qty: N" / "$amt" — either "Qty: N | $amt" or split lines
+    for i, ln in enumerate(lines):
+        m = re.fullmatch(r"Qty: (\d+)(?: \| \$([\d,]+\.\d{2}))?", ln)
+        if not m:
+            continue
+        qty = int(m.group(1))
+        total = _money(m.group(2)) if m.group(2) else None
+        if total is None and i + 1 < len(lines):
+            m2 = re.fullmatch(r"\$([\d,]+\.\d{2})", lines[i + 1])
+            total = _money(m2.group(1)) if m2 else None
+        desc = _clean_desc(lines[i - 1]) if i > 0 else None
+        if total is not None:
+            items.append({
+                "description": desc,
+                "quantity": qty,
+                "unit_price": round(total / qty, 2) if qty else total,
+                "total": total,
+            })
+    return items
+
+
+def _find_payment_method(full, lines):
+    ptype, last4 = None, None
+    # last4 markers: "x-1234", "x- 1234", "••1234", "**1234", "ending in 1234"
+    m = re.search(r"(?:x-\s?|••\s?|\*\*\s?|ending in )(\d{4})", full)
+    if m:
+        last4 = m.group(1)
+    # type from funding-source context
+    ctx = ""
+    anchor = re.search(
+        r"(Funding Sources Used[^\n]*|Paid with:|Payment method:|Paid [^\n]+ with)\n((?:[^\n]*\n){0,4})",
+        full,
+    )
+    if anchor:
+        ctx = anchor.group(0)
+    hay = ctx or full
+    if re.search(r"PayPal Balance", hay):
+        ptype = "paypal_balance"
+    if re.search(r"(?i)(visa|mastercard|amex|american express|discover|credit card|debit card)", hay):
+        ptype = "card"
+    elif re.search(r"(?i)(bank|checking|savings)", hay):
+        ptype = "bank"
+    elif ptype is None and last4:
+        ptype = "unknown"
+    if ptype is None and re.search(r"PayPal Balance", full):
+        ptype = "paypal_balance"
+    return ({"type": ptype, "card_last4": last4} if (ptype or last4) else None)
+
+
+# ---------------------------------------------------------------- main parse
+def parse_eml(path):
+    with open(path, "rb") as f:
+        msg = BytesParser(policy=policy.default).parse(f)
+
+    body = msg.get_body(preferencelist=("html", "plain"))
+    content = body.get_content() if body else ""
+    if body and body.get_content_type() == "text/html":
+        lines = html_to_lines(content)
+    else:
+        lines = [re.sub(r"\s+", " ", l).strip() for l in content.split("\n") if l.strip()]
+    full = "\n".join(lines)
+
+    subject = str(msg["subject"] or "").replace("\xa0", " ")
+    from_addr = str(msg["from"] or "")
+    dom = from_addr.split("@")[-1].strip(">").lower() if "@" in from_addr else None
+
+    merchant = _find_merchant(subject, full, lines)
+    date = _find_date(full, lines, msg)
+
+    txn = re.search(r"Transaction ID:?\s*\n?\s*([0-9A-Z]{17})", full)
+    order_id = txn.group(1) if txn else None
+    inv = re.search(r"Invoice ID:?\s*\n?\s*([^\s|][^\n|]*)", full)
+    invoice_id = inv.group(1).strip() if inv else None
+
+    grand_total = _find_amount_after(r"\nTotal", full)
+    if grand_total is None:
+        grand_total = _find_amount_after(
+            r"\n(?:Your total charge:?|You paid|Money sent|Amount you'll pay)", full)
+    if grand_total is None:
+        m = re.search(
+            r"You (?:sent(?: a payment (?:of|for))?|paid|authorized a payment of) " + AMT, full)
+        grand_total = _money(m.group(1)) if m else None
+    subtotal = _find_amount_after(r"\nSubtotal", full)
+    tax_m = re.search(r"\n((?:Sales )?Tax)[^\n]*?\|\s*" + AMT, full)
+    tax = _money(tax_m.group(2)) if tax_m else None
+
+    items = _find_items(lines)
+    payment_method = _find_payment_method(full, lines)
+
+    currency = None
+    cm = re.search(r"\$[\d,\.]+ ?(USD|EUR|GBP|CAD|AUD|JPY)", full) or re.search(r"(USD|EUR|GBP)", subject)
+    if cm:
+        currency = cm.group(1)
+
+    # raw_ref from optional sidecar written at extraction time
+    raw_ref = {"mbox_file": None, "byte_offset": None}
+    side = path + ".ref.json"
+    if os.path.exists(side):
+        with open(side) as f:
+            ref = json.load(f)
+        raw_ref = {"mbox_file": ref.get("mbox_file"), "byte_offset": ref.get("byte_offset")}
+
+    return {
+        "source": "email",
+        "message_id": str(msg["message-id"] or "").strip("<> ") or None,
+        "sender_domain": dom,
+        "merchant_name": merchant,
+        "date": date,
+        "order_id": order_id,
+        "invoice_id": invoice_id,   # extra: PayPal has both txn id and merchant invoice id
+        "grand_total": grand_total,
+        "subtotal": subtotal,
+        "tax": tax,
+        "tip": None,
+        "item_count": len(items),
+        "items": items,
+        "payment_method": payment_method,
+        "currency": currency,
+        "raw_ref": raw_ref,
+    }
+
+
+if __name__ == "__main__":
+    outs = [parse_eml(p) for p in sys.argv[1:]]
+    print(json.dumps(outs[0] if len(outs) == 1 else outs, indent=2, ensure_ascii=False))
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_pos_restaurants.py b/infra/email_receipt_inbox/lambdas/parsers/parse_pos_restaurants.py
new file mode 100644
index 000000000..19adde39a
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_pos_restaurants.py
@@ -0,0 +1,444 @@
+#!/usr/bin/env python3
+"""Prototype parser for POS restaurant e-receipts (Toast, Square, SpotOn, Clover).
+
+Usage: python3 parse_pos-restaurants.py file.eml [file2.eml ...]
+Emits one JSON object per input file (to stdout, or use --out DIR).
+
+Providers handled:
+  toasttab.com  - "Online Order Receipt", "Order & Pay Receipt", "Tell us how we did" review receipts
+  squareup.com  - "Receipt from X" (2015-2026), "Your order from X" pickup confirmations
+  spoton.com    - "Pickup Order Confirmation" receipts
+  clover.com    - stub receipts (merchant/date/total only; itemization is behind a web link)
+"""
+import argparse
+import json
+import os
+import re
+import sys
+from email import policy
+from email.parser import BytesParser
+from email.utils import parsedate_to_datetime
+from html.parser import HTMLParser
+
+MONEY = re.compile(r"^[\$£€]\s?(-?\d[\d,]*\.\d{2})$")
+MONEY_ANY = re.compile(r"[\$£€]\s?(-?\d[\d,]*\.\d{2})")
+CARD_BRANDS = r"(Visa|Master[Cc]ard|MASTERCARD|Amex|American Express|Discover|JCB|Diners|Union ?Pay|Interac)"
+CARD_INLINE = re.compile(CARD_BRANDS + r"[^\d]{0,30}(\d{4})\b")
+CARD_BRAND_ONLY = re.compile(r"^" + CARD_BRANDS + r"$")
+CARD_MASKED = re.compile(r"^[xX*•]{4,}\s?(\d{4})$")
+
+
+class TextLines(HTMLParser):
+    """Flatten HTML into a list of visible text lines (one per block-ish element)."""
+
+    BLOCKS = {"br", "tr", "p", "div", "td", "li", "h1", "h2", "h3", "h4", "table"}
+
+    def __init__(self):
+        super().__init__(convert_charrefs=True)
+        self.lines, self.cur, self.skip = [], [], 0
+
+    def _flush(self):
+        t = " ".join("".join(self.cur).split())
+        if t:
+            self.lines.append(t)
+        self.cur = []
+
+    def handle_starttag(self, tag, attrs):
+        if tag in ("style", "script", "head", "title"):
+            self.skip += 1
+        if tag in self.BLOCKS:
+            self._flush()
+
+    def handle_endtag(self, tag):
+        if tag in ("style", "script", "head", "title"):
+            self.skip = max(0, self.skip - 1)
+        if tag in self.BLOCKS:
+            self._flush()
+
+    def handle_data(self, data):
+        if not self.skip:
+            self.cur.append(data)
+
+
+def html_to_lines(html_text):
+    p = TextLines()
+    p.feed(html_text)
+    p._flush()
+    out = []
+    for l in p.lines:
+        if not out or out[-1] != l:
+            out.append(l)
+    return out
+
+
+def load_email(path):
+    with open(path, "rb") as f:
+        msg = BytesParser(policy=policy.default).parse(f)
+    html_body, plain_body = None, None
+    for part in msg.walk():
+        ct = part.get_content_type()
+        try:
+            if ct == "text/html" and html_body is None:
+                html_body = part.get_content()
+            elif ct == "text/plain" and plain_body is None:
+                plain_body = part.get_content()
+        except Exception:
+            pass
+    if html_body:
+        lines = html_to_lines(html_body)
+    elif plain_body:
+        lines = [" ".join(l.split()) for l in plain_body.splitlines() if l.strip()]
+    else:
+        lines = []
+    return msg, lines
+
+
+def money(s):
+    m = MONEY_ANY.search(s or "")
+    return float(m.group(1).replace(",", "")) if m else None
+
+
+def currency_of(lines):
+    joined = " ".join(lines)
+    if "£" in joined:
+        return "GBP"
+    if "€" in joined:
+        return "EUR"
+    return "USD"
+
+
+def find_amount_after(lines, label_re, start=0, window=2):
+    """Return amount on a line matching label_re (inline) or within `window` following lines."""
+    rx = re.compile(label_re, re.I)
+    for i in range(start, len(lines)):
+        if rx.search(lines[i]):
+            amt = money(lines[i])
+            if amt is not None and not rx.fullmatch(lines[i]):
+                pass  # label and amount inline
+            if amt is not None:
+                return amt, i
+            for j in range(i + 1, min(i + 1 + window, len(lines))):
+                amt = money(lines[j])
+                if amt is not None:
+                    return amt, j
+    return None, None
+
+
+def extract_card(lines):
+    for i, l in enumerate(lines):
+        m = CARD_INLINE.search(l)
+        if m:
+            return m.group(1).title().replace("Mastercard", "Mastercard"), m.group(2)
+        m = CARD_BRAND_ONLY.match(l)
+        if m and i + 1 < len(lines):
+            m2 = CARD_MASKED.match(lines[i + 1])
+            if m2:
+                return m.group(1).title(), m2.group(1)
+    return None, None
+
+
+# ---------------------------------------------------------------- Toast
+def parse_toast(msg, lines, subject):
+    out = {}
+    m = re.search(r"Receipt for \$?[\d.,]* at (.+?)\s*$", subject)
+    if not m:
+        m = re.search(r"review your receipt for Order #\d+ at (.+?)(?: - .*)?$", subject)
+    if not m:
+        m = re.search(r"Receipt for Order #\d+ at (.+?)(?: - .*)?$", subject)
+    if m:
+        out["merchant_name"] = m.group(1).strip()
+    else:
+        # first line is usually "Merchant - phone"
+        for l in lines[:5]:
+            mm = re.match(r"^(.*?)\s*-\s*[\d()\- .]{7,}$", l)
+            if mm:
+                out["merchant_name"] = mm.group(1).strip()
+                break
+
+    m2 = next((re.search(r"Check #(\w+)", l) for l in lines if re.search(r"Check #\w+", l)), None)
+    if m2:
+        out["order_id"] = "Check #" + m2.group(1)
+
+    # items: between "Ordered:" (or "How was your visit?") and "Subtotal"
+    try:
+        end = next(i for i, l in enumerate(lines) if re.fullmatch(r"Subtotal", l, re.I))
+    except StopIteration:
+        end = None
+    items = []
+    if end is not None:
+        start = 0
+        for i, l in enumerate(lines[:end]):
+            if re.fullmatch(r"Ordered:", l) or "How was your visit" in l:
+                start = i + 1
+        i = start
+        pending = None  # (name, qty)
+        skip_rx = re.compile(
+            r"^(Due:|Ordered:|Server:|Table \d+|Check #|How was your visit|The restaurant tracks|"
+            r"\d{1,2}/\d{1,2}/\d{2,4}.*[AP]M|Pick up|Take ?Out|Delivery|Dine In|.*\(Online\)$)",
+            re.I,
+        )
+        while i < end:
+            l = lines[i]
+            if MONEY.match(l):
+                if pending:
+                    name, qty = pending
+                    price = money(l)
+                    items.append(
+                        {
+                            "description": name,
+                            "quantity": qty,
+                            "unit_price": round(price / qty, 2) if qty else price,
+                            "total": price,
+                        }
+                    )
+                    pending = None
+            elif not skip_rx.match(l) and l != out.get("merchant_name"):
+                qm = re.match(r"^(\d+)\s+(.+)$", l)
+                if qm:
+                    pending = (qm.group(2), int(qm.group(1)))
+                else:
+                    # modifier line (e.g. "$Grilled Chicken", "Avocado")
+                    pending = (l.lstrip("$"), 1)
+            i += 1
+
+    out["items"] = items
+    out["subtotal"], _ = find_amount_after(lines, r"^Subtotal$")
+    out["tax"], _ = find_amount_after(lines, r"^Tax(es)?$")
+    out["tip"], _ = find_amount_after(lines, r"^Tip$")
+    out["grand_total"], _ = find_amount_after(lines, r"^Total$")
+    return out
+
+
+# ---------------------------------------------------------------- Square
+def parse_square(msg, lines, subject):
+    out = {}
+    m = re.search(r"Receipt from (.+)$", subject) or re.search(r"Your order from (.+)$", subject)
+    if m:
+        out["merchant_name"] = m.group(1).strip()
+    else:
+        for l in lines:
+            mm = re.search(r"Receipt for \$[\d.,]+ at (.+?) on ", l)
+            if mm:
+                out["merchant_name"] = mm.group(1)
+                break
+
+    # receipt code (#FGcX) and/or Order #
+    order_id = None
+    for l in lines:
+        mm = re.search(r"Order #:?\s*(\d+)", l)
+        if mm:
+            order_id = "Order #" + mm.group(1)
+            break
+    if not order_id:
+        for l in lines:
+            if re.fullmatch(r"#\w{4,6}", l):
+                order_id = l
+                break
+    out["order_id"] = order_id
+
+    # items: (name [× N]) followed by $price, before Total/Subtotal footer
+    stop_rx = re.compile(
+        r"^(Purchase Subtotal|Subtotal|Total|Tip|Tax|Sales Tax|Shop Online|Receipt Settings)$", re.I
+    )
+    tax_name_rx = re.compile(r"\(\d+(\.\d+)?%\)")
+    items = []
+    pending = None
+    for l in lines:
+        if stop_rx.match(l) and items:
+            break
+        if MONEY.match(l):
+            if pending:
+                name = pending
+                qty = 1
+                qm = re.search(r"^(.*?)\s*[×x]\s*(\d+)$", name)
+                if qm:
+                    name, qty = qm.group(1).strip(), int(qm.group(2))
+                price = money(l)
+                items.append(
+                    {
+                        "description": name,
+                        "quantity": qty,
+                        "unit_price": round(price / qty, 2) if qty else price,
+                        "total": price,
+                    }
+                )
+                pending = None
+        else:
+            junk = (
+                stop_rx.match(l)
+                or tax_name_rx.search(l)
+                or re.search(r"Square Receipt|automatically sends|How was your experience|Order confirmed|"
+                             r"Thank you|Estimated Pickup|Pickup location|Pickup instructions|Order status|"
+                             r"Receipt for \$|reply to this email|leave feedback", l, re.I)
+                or l == out.get("merchant_name")
+            )
+            pending = None if junk else l
+    out["items"] = items
+
+    out["subtotal"], _ = find_amount_after(lines, r"^(Purchase )?Subtotal$")
+    tax, _ = find_amount_after(lines, r"^(Sales )?Tax(es)?$")
+    if tax is None:
+        # modern Square labels tax as e.g. "California (7.25%)"
+        for i, l in enumerate(lines):
+            if tax_name_rx.search(l) and "CC" not in l:
+                tax = money(l)
+                if tax is None and i + 1 < len(lines):
+                    tax = money(lines[i + 1])
+                if tax is not None:
+                    break
+    out["tax"] = tax
+    out["tip"], _ = find_amount_after(lines, r"^Tip$")
+    out["grand_total"], _ = find_amount_after(lines, r"^Total$")
+    if out["grand_total"] is None:
+        for l in lines:
+            mm = re.search(r"Receipt for \$([\d.,]+) at ", l)
+            if mm:
+                out["grand_total"] = float(mm.group(1).replace(",", ""))
+                break
+    return out
+
+
+# ---------------------------------------------------------------- SpotOn
+def parse_spoton(msg, lines, subject):
+    out = {}
+    m = re.search(r"from (.+?)(?:\.|$)", subject)
+    for l in lines:
+        mm = re.search(r"order from (.+?)\. Check out", l)
+        if mm:
+            out["merchant_name"] = mm.group(1).strip()
+            break
+    if "merchant_name" not in out and m:
+        out["merchant_name"] = m.group(1).strip()
+
+    for l in lines:
+        mm = re.search(r"Order #([\w-]+)", l)
+        if mm:
+            out["order_id"] = "Order #" + mm.group(1)
+            break
+
+    # items: "Nx" line, then name, then $price; modifier lines in between are noted
+    items = []
+    try:
+        start = next(i for i, l in enumerate(lines) if l == "Order Details")
+    except StopIteration:
+        start = 0
+    qty = None
+    name = None
+    for l in lines[start:]:
+        if re.match(r"^(Item total|Sales Tax|Total Charged)", l, re.I):
+            break
+        qm = re.fullmatch(r"(\d+)x", l)
+        if qm:
+            qty, name = int(qm.group(1)), None
+            continue
+        if qty is not None:
+            if MONEY.match(l):
+                if name:
+                    price = money(l)
+                    items.append(
+                        {
+                            "description": name,
+                            "quantity": qty,
+                            "unit_price": round(price / qty, 2),
+                            "total": price,
+                        }
+                    )
+                qty, name = None, None
+            elif name is None:
+                name = l
+            # extra lines after price & before next "Nx" are modifiers; ignored
+    out["items"] = items
+    out["subtotal"], _ = find_amount_after(lines, r"^Item total$")
+    out["tax"], _ = find_amount_after(lines, r"^Sales Tax$")
+    out["tip"], _ = find_amount_after(lines, r"^Tip$")
+    out["grand_total"], _ = find_amount_after(lines, r"^Total Charged$")
+    return out
+
+
+# ---------------------------------------------------------------- Clover
+def parse_clover(msg, lines, subject):
+    out = {"items": [], "subtotal": None, "tax": None, "tip": None}
+    m = re.search(r"Your receipt from (.+)$", subject)
+    if m:
+        out["merchant_name"] = m.group(1).strip()
+    for l in lines:
+        if MONEY.match(l):
+            out["grand_total"] = money(l)
+            break
+    out["order_id"] = None
+    return out
+
+
+# ---------------------------------------------------------------- driver
+def parse_eml(path, raw_ref=None):
+    msg, lines = load_email(path)
+    from_addr = str(msg.get("From", ""))
+    dm = re.search(r"@([\w.\-]+)", from_addr)
+    sender_domain = dm.group(1).lower() if dm else None
+    subject = str(msg.get("Subject", "")).replace("\n", " ").strip()
+
+    root = ".".join(sender_domain.split(".")[-2:]) if sender_domain else ""
+    if root == "toasttab.com":
+        parsed = parse_toast(msg, lines, subject)
+    elif root in ("squareup.com", "square.com"):
+        parsed = parse_square(msg, lines, subject)
+    elif root == "spoton.com":
+        parsed = parse_spoton(msg, lines, subject)
+    elif root == "clover.com":
+        parsed = parse_clover(msg, lines, subject)
+    else:
+        parsed = {"items": []}
+
+    try:
+        date_iso = parsedate_to_datetime(msg["Date"]).isoformat()
+    except Exception:
+        date_iso = None
+
+    brand, last4 = extract_card(lines)
+    items = parsed.get("items") or []
+    result = {
+        "source": "email",
+        "message_id": str(msg.get("Message-ID", "")).strip() or None,
+        "sender_domain": sender_domain,
+        "merchant_name": parsed.get("merchant_name"),
+        "date": date_iso,
+        "order_id": parsed.get("order_id"),
+        "grand_total": parsed.get("grand_total"),
+        "subtotal": parsed.get("subtotal"),
+        "tax": parsed.get("tax"),
+        "tip": parsed.get("tip"),
+        "item_count": len(items) or None,
+        "items": items,
+        "payment_method": {"type": brand.lower() if brand else ("card" if last4 else None), "card_last4": last4},
+        "currency": currency_of(lines),
+        "raw_ref": raw_ref or {"mbox_file": None, "byte_offset": None},
+    }
+    return result
+
+
+def main():
+    ap = argparse.ArgumentParser()
+    ap.add_argument("eml", nargs="+")
+    ap.add_argument("--out", help="directory for per-file .json outputs")
+    args = ap.parse_args()
+    for p in args.eml:
+        ref = None
+        sidecar = p + ".ref.json"
+        if os.path.exists(sidecar):
+            with open(sidecar) as f:
+                rr = json.load(f)
+            ref = {"mbox_file": rr.get("mbox_file"), "byte_offset": rr.get("byte_offset")}
+        res = parse_eml(p, raw_ref=ref)
+        text = json.dumps(res, indent=2, ensure_ascii=False)
+        if args.out:
+            os.makedirs(args.out, exist_ok=True)
+            name = os.path.splitext(os.path.basename(p))[0] + ".json"
+            with open(os.path.join(args.out, name), "w") as f:
+                f.write(text + "\n")
+            print(f"wrote {name}", file=sys.stderr)
+        else:
+            print(text)
+
+
+if __name__ == "__main__":
+    main()
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_restaurant_platforms.py b/infra/email_receipt_inbox/lambdas/parsers/parse_restaurant_platforms.py
new file mode 100644
index 000000000..384a0e120
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_restaurant_platforms.py
@@ -0,0 +1,447 @@
+#!/usr/bin/env python3
+"""Prototype parser for restaurant online-ordering platform e-receipts.
+
+Usage: python3 parse_restaurant_platforms.py file.eml [file2.eml ...]
+Emits one JSON object per input file (to stdout, or use --out DIR).
+
+Emits the receipts-online email schema:
+{ source, message_id, sender_domain, merchant_name, platform, date, order_id,
+  grand_total, subtotal, tax, tip, item_count, items[], payment_method,
+  currency, raw_ref }
+
+`merchant_name` is the RESTAURANT (or, for SCE, the utility); `platform` names
+the online-ordering service the confirmation came through.
+
+Platforms handled (dispatch by sender_domain):
+  chownow.com        - "Order Receipt" itemized confirmations ("Thanks for your
+                       order (#...)"), plus pickup-ready notifications and
+                       refunds (no itemization, refund total is negative).
+  dylish.com         - "[<Restaurant> #<id>] Your order is confirmed/ready/sent"
+                       itemized; price line is FOLLOWED by an "xN" qty line.
+  oftendining.com    - "Order Confirmation: OD-<id>" itemized; name/$price pairs,
+                       no per-item qty, "Sub Total"/"<region> Tax"/"Total".
+  scewebservices.com - Southern California Edison (a UTILITY, grouped here by
+                       domain). "Payment Confirmation" (Amount Paid) and
+                       "<Month> SCE Bill is Ready to View" (Amount Due). Other
+                       subjects (app-login, service-request, verify-email) are
+                       not receipts and yield no total.
+"""
+import argparse
+import json
+import os
+import re
+import sys
+from email import policy
+from email.parser import BytesParser
+from email.utils import parsedate_to_datetime
+from html.parser import HTMLParser
+
+MONEY = re.compile(r"^[\$£€]\s?(-?\d[\d,]*\.\d{2})$")
+MONEY_ANY = re.compile(r"[\$£€]\s?(-?\d[\d,]*\.\d{2})")
+BARE_DECIMAL = re.compile(r"^(-?\d[\d,]*\.\d{2})$")
+
+
+class TextLines(HTMLParser):
+    """Flatten HTML into a list of visible text lines (one per block-ish element)."""
+
+    BLOCKS = {"br", "tr", "p", "div", "td", "li", "h1", "h2", "h3", "h4", "table"}
+
+    def __init__(self):
+        super().__init__(convert_charrefs=True)
+        self.lines, self.cur, self.skip = [], [], 0
+
+    def _flush(self):
+        t = " ".join("".join(self.cur).split())
+        if t:
+            self.lines.append(t)
+        self.cur = []
+
+    def handle_starttag(self, tag, attrs):
+        if tag in ("style", "script", "head", "title"):
+            self.skip += 1
+        if tag in self.BLOCKS:
+            self._flush()
+
+    def handle_endtag(self, tag):
+        if tag in ("style", "script", "head", "title"):
+            self.skip = max(0, self.skip - 1)
+        if tag in self.BLOCKS:
+            self._flush()
+
+    def handle_data(self, data):
+        if not self.skip:
+            self.cur.append(data)
+
+
+def html_to_lines(html_text):
+    p = TextLines()
+    p.feed(html_text)
+    p._flush()
+    out = []
+    for l in p.lines:
+        # strip zero-width / soft-hyphen preheader junk
+        l = re.sub(r"[​‌‍­͏﻿]", "", l).strip()
+        if l and (not out or out[-1] != l):
+            out.append(l)
+    return out
+
+
+def load_email(path):
+    with open(path, "rb") as f:
+        msg = BytesParser(policy=policy.default).parse(f)
+    html_body, plain_body = None, None
+    for part in msg.walk():
+        ct = part.get_content_type()
+        try:
+            if ct == "text/html" and html_body is None:
+                html_body = part.get_content()
+            elif ct == "text/plain" and plain_body is None:
+                plain_body = part.get_content()
+        except Exception:
+            pass
+    if html_body:
+        lines = html_to_lines(html_body)
+    elif plain_body:
+        lines = [" ".join(l.split()) for l in plain_body.splitlines() if l.strip()]
+    else:
+        lines = []
+    return msg, lines
+
+
+def money(s):
+    m = MONEY_ANY.search(s or "")
+    return float(m.group(1).replace(",", "")) if m else None
+
+
+def from_display_name(msg):
+    raw = str(msg.get("From", ""))
+    m = re.match(r'\s*"?([^"<]+?)"?\s*<', raw)
+    if m:
+        return m.group(1).strip()
+    return None
+
+
+def find_amount_after(lines, label_re, start=0, window=2):
+    """Amount on a line matching label_re (inline) or within `window` following lines."""
+    rx = re.compile(label_re, re.I)
+    for i in range(start, len(lines)):
+        if rx.search(lines[i]):
+            amt = money(lines[i])
+            if amt is not None:
+                return amt, i
+            for j in range(i + 1, min(i + 1 + window, len(lines))):
+                amt = money(lines[j])
+                if amt is not None:
+                    return amt, j
+    return None, None
+
+
+# ---------------------------------------------------------------- ChowNow
+def parse_chownow(msg, lines, subject):
+    out = {"platform": "ChowNow", "items": []}
+
+    # merchant: From display name is the restaurant (except platform-sent refunds)
+    disp = from_display_name(msg)
+    merchant = disp if disp and disp.lower() != "chownow" else None
+    if not merchant:
+        m = re.search(r"Order refund from (.+?)\s*\(#", subject)
+        if m:
+            merchant = m.group(1).strip()
+    if not merchant:
+        for l in lines:
+            m = re.search(r"submitted to (.+?)\.", l) or re.search(r"refunded by (.+?)\.", l)
+            if m:
+                merchant = m.group(1).strip()
+                break
+    out["merchant_name"] = merchant
+
+    # order id
+    order_id = None
+    m = re.search(r"\(#(\d+)\)", subject) or re.search(r"(?:ORDER #|order #)(\d+)", " ".join(lines))
+    if m:
+        order_id = m.group(1)
+    out["order_id"] = order_id
+
+    is_refund = "refund" in subject.lower() or any("Refund Amount" in l for l in lines)
+
+    # totals
+    out["subtotal"], _ = find_amount_after(lines, r"^Sub-?total:?$")
+    out["tax"], _ = find_amount_after(lines, r"^Taxes?:?$")
+    out["tip"], _ = find_amount_after(lines, r"^Tip:?$")
+    if is_refund:
+        amt, _ = find_amount_after(lines, r"^Refund Amount$")
+        out["grand_total"] = -amt if amt is not None else None
+    else:
+        out["grand_total"], _ = find_amount_after(lines, r"^Total:?$")
+
+    # items: between order-details header and Sub-total; blocks delimited by $price
+    try:
+        start = next(i for i, l in enumerate(lines)
+                     if re.search(r"Details for order|Order Receipt", l))
+    except StopIteration:
+        start = 0
+    try:
+        end = next(i for i, l in enumerate(lines) if re.match(r"^Sub-?total", l, re.I))
+    except StopIteration:
+        end = len(lines)
+    block, qty = [], 1
+    for l in lines[start + 1:end]:
+        if re.fullmatch(r"\d+", l):  # standalone qty line precedes the item name
+            qty = int(l)
+            block = []  # discard intro / "Details for order" text before the item
+            continue
+        if MONEY.match(l):
+            price = money(l)
+            names = [b for b in block if b.lower() not in ("additions", "add-ons", "options")]
+            desc = " ".join(names).strip() or (block[0] if block else None)
+            if desc:
+                out["items"].append({
+                    "description": desc, "quantity": qty,
+                    "unit_price": round(price / qty, 2) if qty else price, "total": price,
+                })
+            block, qty = [], 1
+        else:
+            block.append(l)
+    return out
+
+
+# ---------------------------------------------------------------- Dylish
+def parse_dylish(msg, lines, subject):
+    out = {"platform": "Dylish", "items": []}
+    m = re.match(r"^\[(.+?)\s+#[\w-]+\]", subject)
+    out["merchant_name"] = m.group(1).strip() if m else None
+
+    order_id = None
+    m = re.search(r"#([\w-]+)\]", subject)
+    if m:
+        order_id = m.group(1)
+    else:
+        for l in lines:
+            mm = re.search(r"ORDER#\s*([\w-]+)", l)
+            if mm:
+                order_id = mm.group(1)
+                break
+    out["order_id"] = order_id
+
+    out["subtotal"], si = find_amount_after(lines, r"^Subtotal$")
+    out["tax"], _ = find_amount_after(lines, r"^Tax( & Fees)?$")
+    out["tip"], _ = find_amount_after(lines, r"^(Restaurant )?Tip$")
+    out["grand_total"], _ = find_amount_after(lines, r"^Total$")
+
+    # items: name [+ modifier lines], $price, then "xN".  Region ends at Subtotal.
+    try:
+        start = next(i for i, l in enumerate(lines) if re.fullmatch(r"Pickup|Delivery", l, re.I))
+    except StopIteration:
+        start = 0
+    end = si if si is not None else len(lines)
+    block = []
+    i = start + 1
+    while i < end:
+        l = lines[i]
+        if MONEY.match(l):
+            price = money(l)
+            qty = 1
+            if i + 1 < len(lines):
+                qm = re.fullmatch(r"x\s*(\d+)", lines[i + 1], re.I)
+                if qm:
+                    qty = int(qm.group(1))
+                    i += 1
+            desc = block[0] if block else None
+            if desc:
+                out["items"].append({
+                    "description": desc, "quantity": qty,
+                    "unit_price": round(price / qty, 2) if qty else price, "total": price,
+                })
+            block = []
+        else:
+            block.append(l)
+        i += 1
+    return out
+
+
+# ---------------------------------------------------------------- OftenDining
+def parse_oftendining(msg, lines, subject):
+    out = {"platform": "OftenDining", "items": []}
+    order_id = None
+    m = re.search(r"Order Confirmation:\s*(OD-\d+)", subject)
+    if m:
+        order_id = m.group(1)
+    out["order_id"] = order_id
+
+    # merchant: line following the "Order Confirmation: OD-..." body line
+    merchant = None
+    for i, l in enumerate(lines):
+        if re.match(r"Order Confirmation:\s*OD-\d+", l) and i + 1 < len(lines):
+            merchant = lines[i + 1].strip()
+            break
+    out["merchant_name"] = merchant
+
+    out["subtotal"], si = find_amount_after(lines, r"^Sub ?Total$")
+    tax, _ = find_amount_after(lines, r"Tax$")
+    out["tax"] = tax
+    out["tip"], _ = find_amount_after(lines, r"^Tip$")
+    out["grand_total"], _ = find_amount_after(lines, r"^Total$")
+
+    # items: name then $price pairs, between order-detail block and "Sub Total"
+    try:
+        start = next(i for i, l in enumerate(lines) if re.match(r"Pickup Time:|Ordered On:", l))
+    except StopIteration:
+        start = 0
+    end = si if si is not None else len(lines)
+    pending = None
+    for l in lines[start:end]:
+        if re.match(r"Pickup Time:|Ordered On:|Type:|Store Invoice", l):
+            continue
+        if MONEY.match(l):
+            if pending:
+                price = money(l)
+                out["items"].append({
+                    "description": pending, "quantity": 1,
+                    "unit_price": price, "total": price,
+                })
+                pending = None
+        elif re.search(r"[A-Za-z]", l) and not re.match(r"^\d", l):
+            pending = l
+    return out
+
+
+# ---------------------------------------------------------------- SCE (utility)
+def parse_sce(msg, lines, subject):
+    out = {"platform": "SCE", "merchant_name": "Southern California Edison",
+           "items": [], "subtotal": None, "tax": None, "tip": None,
+           "order_id": None, "grand_total": None}
+    joined = " ".join(lines)
+    low = subject.lower()
+
+    if "payment" in low or any("Amount Paid" in l for l in lines):
+        # newer layout: "Amount Paid" label then "$26.62" on a following line
+        amt, _ = find_amount_after(lines, r"^Amount Paid$", window=2)
+        if amt is None:
+            # old column layout: first bare decimal after the headers
+            for l in lines:
+                if BARE_DECIMAL.match(l):
+                    amt = float(l.replace(",", ""))
+                    break
+        out["grand_total"] = amt
+        # confirmation number: labelled, else a 12-digit value beginning with 3
+        conf = None
+        for i, l in enumerate(lines):
+            if l == "Confirmation Number":
+                for j in range(i + 1, min(i + 4, len(lines))):
+                    if re.fullmatch(r"\d{9,}", lines[j]):
+                        conf = lines[j]
+                        break
+            if conf:
+                break
+        if not conf:
+            conf = next((l for l in lines if re.fullmatch(r"3\d{11}", l)), None)
+        out["order_id"] = conf
+    elif "bill" in low or any("Amount Due" in l for l in lines):
+        amt, _ = find_amount_after(lines, r"^Amount Due$", window=5)
+        if amt is None:
+            amt = next((money(l) for l in lines if MONEY_ANY.search(l)), None)
+        out["grand_total"] = amt
+
+    m = re.search(r"(?:X{4,}|\*{4,})(\d{4})\b", joined)
+    last4 = m.group(1) if m else None
+    ptype = "bank account" if re.search(r"Checking Account|Bank Account", joined, re.I) else None
+    out["payment_method"] = {"type": ptype, "card_last4": last4} if (ptype or last4) else None
+    return out
+
+
+# ---------------------------------------------------------------- driver
+def _card_from_lines(lines):
+    joined = " ".join(lines)
+    m = re.search(r"(MasterCard|Master[Cc]ard|Visa|Amex|American Express|Discover)"
+                  r"[^\d]{0,20}(?:\*{2,}|ending in|x{4})?\s*(\d{4})\b", joined)
+    if m:
+        return m.group(1).replace("Master card", "Mastercard"), m.group(2)
+    m = re.search(r"(Saved Credit Card|Credit Card|Nintendo eShop Funds|Apple Pay)", joined)
+    if m:
+        return m.group(1), None
+    return None, None
+
+
+DISPATCH = [
+    ("chownow", parse_chownow),
+    ("dylish", parse_dylish),
+    ("oftendining", parse_oftendining),
+    ("scewebservices", parse_sce),
+]
+
+
+def parse(path, raw_ref=None):
+    msg, lines = load_email(path)
+    from_addr = str(msg.get("From", ""))
+    dm = re.search(r"@([\w.\-]+)", from_addr)
+    sender_domain = dm.group(1).lower() if dm else None
+    subject = str(msg.get("Subject", "")).replace("\n", " ").strip()
+
+    parsed, handler = {"items": []}, None
+    dom = sender_domain or ""
+    for key, fn in DISPATCH:
+        if key in dom:
+            handler = fn
+            break
+    if handler:
+        parsed = handler(msg, lines, subject)
+
+    try:
+        date_iso = parsedate_to_datetime(msg["Date"]).isoformat()
+    except Exception:
+        date_iso = None
+
+    # payment: use handler's if present, else sniff card
+    pm = parsed.get("payment_method", "unset")
+    if pm == "unset":
+        brand, last4 = _card_from_lines(lines)
+        pm = {"type": brand, "card_last4": last4} if (brand or last4) else None
+
+    items = parsed.get("items") or []
+    currency = "GBP" if "£" in "".join(lines) else "EUR" if "€" in "".join(lines) else "USD"
+    return {
+        "source": "email",
+        "message_id": str(msg.get("Message-ID", "")).strip() or None,
+        "sender_domain": sender_domain,
+        "merchant_name": parsed.get("merchant_name"),
+        "platform": parsed.get("platform"),
+        "date": date_iso,
+        "order_id": parsed.get("order_id"),
+        "grand_total": parsed.get("grand_total"),
+        "subtotal": parsed.get("subtotal"),
+        "tax": parsed.get("tax"),
+        "tip": parsed.get("tip"),
+        "item_count": len(items) or None,
+        "items": items,
+        "payment_method": pm,
+        "currency": currency,
+        "raw_ref": raw_ref or {"mbox_file": None, "byte_offset": None},
+    }
+
+
+def main():
+    ap = argparse.ArgumentParser()
+    ap.add_argument("eml", nargs="+")
+    ap.add_argument("--out", help="directory for per-file .json outputs")
+    args = ap.parse_args()
+    for p in args.eml:
+        ref = None
+        sidecar = p + ".ref.json"
+        if os.path.exists(sidecar):
+            with open(sidecar) as f:
+                rr = json.load(f)
+            ref = {"mbox_file": rr.get("mbox_file"), "byte_offset": rr.get("byte_offset")}
+        res = parse(p, raw_ref=ref)
+        text = json.dumps(res, indent=2, ensure_ascii=False)
+        if args.out:
+            os.makedirs(args.out, exist_ok=True)
+            name = os.path.splitext(os.path.basename(p))[0] + ".json"
+            with open(os.path.join(args.out, name), "w") as f:
+                f.write(text + "\n")
+            print(f"wrote {name}", file=sys.stderr)
+        else:
+            print(text)
+
+
+if __name__ == "__main__":
+    main()
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_retail.py b/infra/email_receipt_inbox/lambdas/parsers/parse_retail.py
new file mode 100644
index 000000000..bb5b41c73
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_retail.py
@@ -0,0 +1,298 @@
+#!/usr/bin/env python3
+"""Prototype parser for retail order-confirmation emails.
+
+Brands: Target, Best Buy, eBay, Starbucks (card reloads).
+Usage: parse_retail.py <path.eml> [more.eml ...]
+Emits one JSON object per file (to stdout, or use --outdir to write .json files).
+
+Schema (aligned with receipts-online summaries):
+{ source, message_id, sender_domain, merchant_name, date, order_id,
+  grand_total, subtotal, tax, tip, item_count, items[], payment_method{type,card_last4},
+  currency, raw_ref{mbox_file, byte_offset} }
+"""
+import json
+import os
+import re
+import sys
+from email import policy
+from email.parser import BytesParser
+from email.utils import parsedate_to_datetime
+from html import unescape
+
+MONEY = r"\$?\s?(-?\d{1,3}(?:,\d{3})*\.\d{2})"
+
+
+def _money(s):
+    m = re.search(MONEY, s)
+    return float(m.group(1).replace(",", "")) if m else None
+
+
+def eml_to_lines(path):
+    with open(path, "rb") as f:
+        msg = BytesParser(policy=policy.default).parse(f)
+    html_part = txt_part = None
+    for p in msg.walk():
+        ct = p.get_content_type()
+        if ct == "text/html" and html_part is None:
+            html_part = p
+        elif ct == "text/plain" and txt_part is None:
+            txt_part = p
+    if html_part is not None:
+        h = html_part.get_content()
+        h = re.sub(r"(?is)<(style|script).*?</\1>", " ", h)
+        h = re.sub(r"(?i)<br\s*/?>", "\n", h)
+        h = re.sub(r"(?i)</(td|tr|p|div|table|h\d|li|span)>", "\n", h)
+        t = re.sub(r"<[^>]+>", " ", h)
+        t = unescape(t)
+    elif txt_part is not None:
+        t = txt_part.get_content()
+    else:
+        t = ""
+    t = re.sub(r"[ \t\xa0‌͏​]+", " ", t)
+    lines = [ln.strip() for ln in t.split("\n")]
+    return msg, [ln for ln in lines if ln]
+
+
+def base_record(msg, path):
+    from_addr = str(msg.get("From", ""))
+    m = re.search(r"@([\w.-]+)", from_addr)
+    domain = m.group(1).lower() if m else None
+    date = None
+    if msg.get("Date"):
+        try:
+            date = parsedate_to_datetime(msg["Date"]).isoformat()
+        except Exception:
+            pass
+    return {
+        "source": "email",
+        "message_id": msg.get("Message-ID"),
+        "sender_domain": domain,
+        "merchant_name": None,
+        "date": date,
+        "order_id": None,
+        "grand_total": None,
+        "subtotal": None,
+        "tax": None,
+        "tip": None,
+        "item_count": None,
+        "items": [],
+        "payment_method": {"type": None, "card_last4": None},
+        "currency": "USD",
+        "raw_ref": {"mbox_file": None, "byte_offset": None, "eml_path": os.path.abspath(path)},
+    }
+
+
+def _next_money(lines, i, lookahead=3):
+    """Value on the same line as a label, or within the next few lines."""
+    for j in range(i, min(len(lines), i + 1 + lookahead)):
+        v = _money(lines[j] if j > i else lines[i])
+        if v is not None:
+            return v
+    return None
+
+
+# ---------------------------------------------------------------- Target
+def parse_target(rec, lines, subject):
+    rec["merchant_name"] = "Target"
+    for src in [subject] + lines[:6]:
+        m = re.search(r"Order\s*#:?\s*(\d{8,})", src or "")
+        if m:
+            rec["order_id"] = m.group(1)
+            break
+    items = []
+    for i, ln in enumerate(lines):
+        m = re.match(r"Qty:\s*(\d+)", ln)
+        if m and i > 0:
+            qty = int(m.group(1))
+            unit = _money(lines[i + 1]) if i + 1 < len(lines) and "/ ea" in lines[i + 1] else None
+            desc = lines[i - 1]
+            if desc and not re.match(r"(Qty|Order|Processing|Delivers)", desc):
+                items.append({
+                    "description": desc,
+                    "quantity": qty,
+                    "unit_price": unit,
+                    "total": round(qty * unit, 2) if unit is not None else None,
+                })
+    rec["items"] = items
+    for i, ln in enumerate(lines):
+        if re.match(r"Subtotal", ln):
+            rec["subtotal"] = _next_money(lines, i)
+        elif re.match(r"Estimated Taxes?$", ln):
+            rec["tax"] = _next_money(lines, i)
+        elif re.match(r"(Total|Order total)$", ln):
+            rec["grand_total"] = _next_money(lines, i)
+    rec["item_count"] = sum(it["quantity"] or 1 for it in items) or None
+    return rec
+
+
+# ---------------------------------------------------------------- Best Buy
+def parse_bestbuy(rec, lines, subject):
+    rec["merchant_name"] = "Best Buy"
+    for src in [subject] + lines:
+        m = re.search(r"(BBY01-\d{9,})", src or "")
+        if m:
+            rec["order_id"] = m.group(1)
+            break
+    # Items: "<name>" / "Model: X" / "SKU: N" / Qty / [Price] / <qty> / $<price>
+    items = []
+    for i, ln in enumerate(lines):
+        m = re.match(r"SKU:\s*(\d+)", ln)
+        if not m:
+            continue
+        # name is 1-2 lines back (skip a Model: line)
+        name = None
+        for j in (i - 1, i - 2):
+            if j >= 0 and not lines[j].startswith("Model:"):
+                name = lines[j]
+                break
+        qty = price = None
+        for j in range(i + 1, min(len(lines), i + 6)):
+            if lines[j].isdigit() and qty is None:
+                qty = int(lines[j])
+            elif _money(lines[j]) is not None and price is None:
+                price = _money(lines[j])
+            if qty is not None and price is not None:
+                break
+        items.append({
+            "description": name,
+            "quantity": qty or 1,
+            "unit_price": price,
+            "total": round((qty or 1) * price, 2) if price is not None else None,
+        })
+    rec["items"] = items
+    for i, ln in enumerate(lines):
+        if re.match(r"Subtotal:?$", ln):
+            rec["subtotal"] = _next_money(lines, i)
+        elif re.match(r"Tax:?\*?$", ln):
+            rec["tax"] = _next_money(lines, i)
+        elif re.match(r"Order Total:?\*?$", ln):
+            rec["grand_total"] = _next_money(lines, i)
+    rec["item_count"] = sum(it["quantity"] or 1 for it in items) or None
+    return rec
+
+
+# ---------------------------------------------------------------- eBay
+def parse_ebay(rec, lines, subject):
+    rec["merchant_name"] = "eBay"
+    for i, ln in enumerate(lines):
+        m = re.search(r"Order number\s*:?\s*([\d-]{8,})?$", ln)
+        if m:
+            oid = m.group(1) or (lines[i + 1] if i + 1 < len(lines) else "")
+            if re.match(r"^[\d-]{8,}$", oid.strip()):
+                rec["order_id"] = oid.strip()
+                break
+    # Items: a title line followed (within 2 lines) by "Price:"/"Total :" label.
+    items = []
+    for i, ln in enumerate(lines):
+        if re.match(r"(Price|Total)\s*:", ln):
+            val = _next_money(lines, i, lookahead=1)
+            # walk back over boilerplate to a plausible title
+            j = i - 1
+            while j >= 0 and re.match(r"(eBay Money Back|Money Back Guarantee)", lines[j]):
+                j -= 1
+            if j >= 0 and val is not None:
+                title = lines[j]
+                if len(title) > 8 and not _money(title) and not re.match(
+                        r"(Order|Your|View|Browse|We|Seller|Estimated|ETA|Thanks)", title):
+                    # qty pattern: "Price (2 x $9.97)"
+                    qm = re.match(r"Price \((\d+) x \$([\d.]+)\)", ln)
+                    qty = int(qm.group(1)) if qm else 1
+                    unit = float(qm.group(2)) if qm else val
+                    if not any(it["description"] == title for it in items):
+                        items.append({"description": title, "quantity": qty,
+                                      "unit_price": unit, "total": val})
+    rec["items"] = items
+    # Totals block after "Order total:"
+    try:
+        start = next(i for i, ln in enumerate(lines) if re.match(r"Order total\s*:", ln))
+    except StopIteration:
+        start = None
+    if start is not None:
+        for i in range(start, min(len(lines), start + 14)):
+            ln = lines[i]
+            if re.match(r"(Subtotal|Price)\b", ln):
+                rec["subtotal"] = _next_money(lines, i, 1)
+            elif re.match(r"Sales tax", ln):
+                rec["tax"] = _next_money(lines, i, 1)
+            elif re.match(r"Total charged to", ln):
+                rec["grand_total"] = _next_money(lines, i, 3)
+    if rec["grand_total"] is None and items:
+        rec["grand_total"] = items[0]["total"]
+    m = re.search(r"x\s?-(\d{4})", "\n".join(lines))
+    if m:
+        rec["payment_method"] = {"type": "card", "card_last4": m.group(1)}
+    rec["item_count"] = sum(it["quantity"] or 1 for it in items) or None
+    return rec
+
+
+# ---------------------------------------------------------------- Starbucks
+def parse_starbucks(rec, lines, subject):
+    rec["merchant_name"] = "Starbucks"
+    text = "\n".join(lines)
+    m = re.search(r"Order Number:\s*([A-Z0-9]{10,})", text)
+    if m:
+        rec["order_id"] = m.group(1)
+    m = re.search(r"Received:\s*\w+,\s*(\w+ \d{1,2}, \d{4})", text)
+    if m:
+        try:
+            from datetime import datetime
+            rec["date"] = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
+        except ValueError:
+            pass
+    def grab(label):
+        m = re.search(label + r":?\s*(?:USD\s*)?\n?\s*(?:USD\s*)?(\d[\d,]*\.\d{2})", text)
+        return float(m.group(1).replace(",", "")) if m else None
+    rec["subtotal"] = grab(r"(?:Merchandise )?Subtotal")
+    rec["tax"] = grab(r"(?:Estimated )?Tax")
+    rec["grand_total"] = grab(r"Total(?: Charge)?")
+    if rec["grand_total"] is None:
+        rec["grand_total"] = rec["subtotal"]
+    amt = rec["grand_total"] or rec["subtotal"]
+    rec["items"] = [{"description": "Starbucks Card Reload", "quantity": 1,
+                     "unit_price": amt, "total": amt}]
+    rec["item_count"] = 1
+    return rec
+
+
+BRANDS = [
+    ("target.com", parse_target),
+    ("bestbuy.com", parse_bestbuy),
+    ("ebay.com", parse_ebay),
+    ("starbucks.com", parse_starbucks),
+]
+
+
+def parse(path):
+    msg, lines = eml_to_lines(path)
+    rec = base_record(msg, path)
+    subject = str(msg.get("Subject", ""))
+    domain = rec["sender_domain"] or ""
+    for suffix, fn in BRANDS:
+        if domain == suffix or domain.endswith("." + suffix):
+            return fn(rec, lines, subject)
+    rec["merchant_name"] = domain
+    return rec
+
+
+def main():
+    args = sys.argv[1:]
+    outdir = None
+    if "--outdir" in args:
+        i = args.index("--outdir")
+        outdir = args[i + 1]
+        args = args[:i] + args[i + 2:]
+        os.makedirs(outdir, exist_ok=True)
+    for p in args:
+        rec = parse(p)
+        js = json.dumps(rec, indent=2, ensure_ascii=False)
+        if outdir:
+            out = os.path.join(outdir, os.path.splitext(os.path.basename(p))[0] + ".json")
+            with open(out, "w") as f:
+                f.write(js + "\n")
+            print(out)
+        else:
+            print(js)
+
+
+if __name__ == "__main__":
+    main()
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_services.py b/infra/email_receipt_inbox/lambdas/parsers/parse_services.py
new file mode 100644
index 000000000..12aeaf0dc
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_services.py
@@ -0,0 +1,369 @@
+#!/usr/bin/env python3
+"""Prototype parser for recurring-service e-receipts (utilities, SaaS, eShop).
+
+Usage: python3 parse_services.py file.eml [file2.eml ...]
+Emits one JSON object per input file (to stdout, or use --out DIR).
+
+Emits the receipts-online email schema:
+{ source, message_id, sender_domain, merchant_name, date, order_id,
+  grand_total, subtotal, tax, tip, item_count, items[], payment_method,
+  currency, raw_ref }
+
+Domains handled (dispatch by sender_domain):
+  socalgas.com          - Southern California Gas. "Bill Ready Notification"
+                          (Total Amount Due / Balance), "One-Time Payment
+                          Confirmation" and "Automatic Monthly Payment is
+                          scheduled" (Payment Amount + Confirmation Number).
+  digitalocean.com      - "Your <Month> invoice is available" and "Thanks for
+                          your payment" (Amount paid). Dunning emails ("We
+                          couldn't process your payment", "Failed to process
+                          card") are NOT receipts and yield no total.
+  stripe.com            - Stripe-powered merchant receipts ("Your receipt from
+                          <Merchant> #...", "Your <Merchant> receipt [#...]").
+                          merchant_name is the actual merchant, not Stripe.
+                          "$X payment to <M> was unsuccessful" are not receipts.
+  accounts.nintendo.com - plain-text eShop receipts: "Confirmation of Digital
+                          Purchase" (Purchased Item + Total) and "Funds Added
+                          to Your Account" (Total). merchant_name = "Nintendo".
+"""
+import argparse
+import json
+import os
+import re
+import sys
+from email import policy
+from email.parser import BytesParser
+from email.utils import parsedate_to_datetime
+from html.parser import HTMLParser
+
+MONEY = re.compile(r"^[\$£€]\s?(-?\d[\d,]*\.\d{2})$")
+MONEY_ANY = re.compile(r"[\$£€]\s?(-?\d[\d,]*\.\d{2})")
+
+
+class TextLines(HTMLParser):
+    BLOCKS = {"br", "tr", "p", "div", "td", "li", "h1", "h2", "h3", "h4", "table"}
+
+    def __init__(self):
+        super().__init__(convert_charrefs=True)
+        self.lines, self.cur, self.skip = [], [], 0
+
+    def _flush(self):
+        t = " ".join("".join(self.cur).split())
+        if t:
+            self.lines.append(t)
+        self.cur = []
+
+    def handle_starttag(self, tag, attrs):
+        if tag in ("style", "script", "head", "title"):
+            self.skip += 1
+        if tag in self.BLOCKS:
+            self._flush()
+
+    def handle_endtag(self, tag):
+        if tag in ("style", "script", "head", "title"):
+            self.skip = max(0, self.skip - 1)
+        if tag in self.BLOCKS:
+            self._flush()
+
+    def handle_data(self, data):
+        if not self.skip:
+            self.cur.append(data)
+
+
+def html_to_lines(html_text):
+    p = TextLines()
+    p.feed(html_text)
+    p._flush()
+    out = []
+    for l in p.lines:
+        l = re.sub(r"[​‌‍­͏﻿]", "", l).strip()
+        if l and (not out or out[-1] != l):
+            out.append(l)
+    return out
+
+
+def load_email(path):
+    with open(path, "rb") as f:
+        msg = BytesParser(policy=policy.default).parse(f)
+    html_body, plain_body = None, None
+    for part in msg.walk():
+        ct = part.get_content_type()
+        try:
+            if ct == "text/html" and html_body is None:
+                html_body = part.get_content()
+            elif ct == "text/plain" and plain_body is None:
+                plain_body = part.get_content()
+        except Exception:
+            pass
+    # Nintendo (and some SCE) are text/plain only; prefer plain there.
+    if plain_body and not html_body:
+        lines = [" ".join(l.split()) for l in plain_body.splitlines() if l.strip()]
+    elif html_body:
+        lines = html_to_lines(html_body)
+    else:
+        lines = []
+    return msg, lines
+
+
+def money(s):
+    m = MONEY_ANY.search(s or "")
+    return float(m.group(1).replace(",", "")) if m else None
+
+
+def from_display_name(msg):
+    raw = str(msg.get("From", ""))
+    m = re.match(r'\s*"?([^"<]+?)"?\s*<', raw)
+    return m.group(1).strip() if m else None
+
+
+def find_amount_after(lines, label_re, start=0, window=2):
+    rx = re.compile(label_re, re.I)
+    for i in range(start, len(lines)):
+        if rx.search(lines[i]):
+            amt = money(lines[i])
+            if amt is not None:
+                return amt, i
+            for j in range(i + 1, min(i + 1 + window, len(lines))):
+                amt = money(lines[j])
+                if amt is not None:
+                    return amt, j
+    return None, None
+
+
+# ---------------------------------------------------------------- SoCalGas
+def parse_socalgas(msg, lines, subject):
+    out = {"merchant_name": "Southern California Gas Company", "items": [],
+           "subtotal": None, "tax": None, "tip": None, "order_id": None,
+           "grand_total": None, "payment_method": None}
+    low = subject.lower()
+
+    if "payment" in low or any("Payment Amount" in l for l in lines):
+        amt, _ = find_amount_after(lines, r"^Payment Amount:?$", window=2)
+        out["grand_total"] = amt
+        for i, l in enumerate(lines):
+            if re.match(r"Confirmation Number", l, re.I):
+                m = re.search(r"(\d{5,})", l)
+                if m:
+                    out["order_id"] = m.group(1)
+                elif i + 1 < len(lines) and re.fullmatch(r"\d{5,}", lines[i + 1]):
+                    out["order_id"] = lines[i + 1]
+                break
+        # bank account name (e.g. "Chase") — no card number in these
+        for i, l in enumerate(lines):
+            if re.match(r"Bank Account:?$", l, re.I) and i + 1 < len(lines):
+                out["payment_method"] = {"type": "bank account (%s)" % lines[i + 1], "card_last4": None}
+                break
+    else:  # bill-ready notification
+        amt, _ = find_amount_after(lines, r"^Total Amount Due", window=2)
+        if amt is None:
+            # credit-balance notices: "Total Balance" / "Balance:" then "$X Credit"
+            amt, _ = find_amount_after(lines, r"^(Total )?Balance", window=2)
+        out["grand_total"] = amt
+    return out
+
+
+# ---------------------------------------------------------------- DigitalOcean
+def parse_digitalocean(msg, lines, subject):
+    out = {"merchant_name": "DigitalOcean", "items": [], "subtotal": None,
+           "tax": None, "tip": None, "order_id": None, "grand_total": None,
+           "payment_method": None}
+    amt, _ = find_amount_after(lines, r"^Amount paid$", window=2)
+    out["grand_total"] = amt
+    if amt is not None:  # only a real receipt carries a subtotal
+        sub, _ = find_amount_after(lines, r"^Usage charges", window=2)
+        out["subtotal"] = sub
+    m = re.search(r"Your (\w+ \d{4}) invoice", subject)
+    if m:
+        out["order_id"] = m.group(1)  # e.g. "June 2022"
+    return out
+
+
+# ---------------------------------------------------------------- Stripe
+def parse_stripe(msg, lines, subject):
+    out = {"items": [], "subtotal": None, "tax": None, "tip": None,
+           "order_id": None, "grand_total": None}
+
+    # merchant: subject "Your receipt from <M> #.." or "Your <M> receipt [#..]"
+    merchant = None
+    m = re.search(r"Your receipt from (.+?)\s*[#\[]", subject)
+    if not m:
+        m = re.search(r"Your (.+?) receipt\b", subject)
+    if m:
+        merchant = m.group(1).strip()
+    if not merchant:
+        disp = from_display_name(msg)
+        merchant = disp if disp and disp.lower() != "stripe" else None
+    if not merchant:
+        for l in lines[:4]:
+            if re.search(r"[A-Za-z]", l) and "receipt" not in l.lower():
+                merchant = l.strip()
+                break
+    out["merchant_name"] = merchant
+
+    # order/receipt number
+    m = re.search(r"#\s?(\d{4}-\d{4})", subject)
+    if not m:
+        for i, l in enumerate(lines):
+            if l == "Receipt number" and i + 1 < len(lines):
+                m = re.match(r"(\S+)", lines[i + 1])
+                break
+    out["order_id"] = m.group(1) if m else None
+
+    # totals
+    amt, _ = find_amount_after(lines, r"^Amount paid$", window=2)
+    if amt is None:
+        amt, _ = find_amount_after(lines, r"^Total$", window=2)
+    if amt is None:
+        # "$7.00 Paid August 17, 2021" hero line
+        for l in lines:
+            if re.search(r"\bPaid\b", l) and MONEY_ANY.search(l):
+                amt = money(l)
+                break
+    out["grand_total"] = amt
+
+    # payment method: "Payment method" then "- 8644", or inline "•••• 8644"
+    last4 = None
+    m = re.search(r"(?:••••|\*{2,}|ending in|-)\s?(\d{4})\b", " ".join(lines))
+    if m:
+        last4 = m.group(1)
+    out["payment_method"] = {"type": "card", "card_last4": last4} if last4 else None
+
+    # best-effort single line item: the description line above "Qty N"
+    for i, l in enumerate(lines):
+        if re.match(r"Qty\s*\d+", l):
+            desc = lines[i - 1] if i > 0 else None
+            price = None
+            for j in range(i, min(i + 3, len(lines))):
+                if MONEY.match(lines[j]):
+                    price = money(lines[j])
+                    break
+            if desc and price is not None and not desc.lower().startswith("receipt"):
+                qm = re.search(r"Qty\s*(\d+)", l)
+                q = int(qm.group(1)) if qm else 1
+                out["items"].append({"description": desc, "quantity": q,
+                                     "unit_price": round(price / q, 2) if q else price,
+                                     "total": price})
+            break
+    return out
+
+
+# ---------------------------------------------------------------- Nintendo
+def parse_nintendo(msg, lines, subject):
+    out = {"merchant_name": "Nintendo", "items": [], "subtotal": None,
+           "tax": None, "tip": None, "order_id": None, "grand_total": None}
+    text = "\n".join(lines)
+
+    m = re.search(r"Transaction ID:\s*(\w+)", text)
+    out["order_id"] = m.group(1) if m else None
+
+    m = re.search(r"^Total:\s*" + MONEY_ANY.pattern, text, re.M)
+    out["grand_total"] = float(m.group(1).replace(",", "")) if m else None
+
+    # payment method
+    ptype = None
+    m = re.search(r"Payment Method:\s*(.+)", text)
+    if m:
+        ptype = m.group(1).strip()
+    elif re.search(r"PayPal:", text):
+        ptype = "PayPal"
+    last4 = None
+    m = re.search(r"(?:ending in|\*{2,})\s?(\d{4})\b", text)
+    if m:
+        last4 = m.group(1)
+    out["payment_method"] = {"type": ptype, "card_last4": last4} if (ptype or last4) else None
+
+    # item: purchased item (digital purchase) or funds-add
+    m = re.search(r"Purchased Item:\s*(.+)", text)
+    desc = m.group(1).strip() if m else None
+    if desc:
+        mm = re.search(r"Purchased Membership:\s*(.+)", text)
+        if mm:
+            desc = mm.group(1).strip()
+        up = re.search(r"Unit Price:\s*" + MONEY_ANY.pattern, text)
+        price = float(up.group(1).replace(",", "")) if up else out["grand_total"]
+        out["items"].append({"description": desc, "quantity": 1,
+                             "unit_price": price, "total": price})
+    elif "Funds Added" in subject or "adding funds" in text:
+        price = out["grand_total"]
+        out["items"].append({"description": "Funds added to Nintendo Account",
+                             "quantity": 1, "unit_price": price, "total": price})
+    return out
+
+
+DISPATCH = [
+    ("socalgas", parse_socalgas),
+    ("digitalocean", parse_digitalocean),
+    ("stripe", parse_stripe),
+    ("nintendo", parse_nintendo),
+]
+
+
+def parse(path, raw_ref=None):
+    msg, lines = load_email(path)
+    from_addr = str(msg.get("From", ""))
+    dm = re.search(r"@([\w.\-]+)", from_addr)
+    sender_domain = dm.group(1).lower() if dm else None
+    subject = str(msg.get("Subject", "")).replace("\n", " ").strip()
+
+    parsed, handler = {"items": []}, None
+    dom = sender_domain or ""
+    for key, fn in DISPATCH:
+        if key in dom:
+            handler = fn
+            break
+    if handler:
+        parsed = handler(msg, lines, subject)
+
+    try:
+        date_iso = parsedate_to_datetime(msg["Date"]).isoformat()
+    except Exception:
+        date_iso = None
+
+    items = parsed.get("items") or []
+    joined = "".join(lines)
+    currency = "GBP" if "£" in joined else "EUR" if "€" in joined else "USD"
+    return {
+        "source": "email",
+        "message_id": str(msg.get("Message-ID", "")).strip() or None,
+        "sender_domain": sender_domain,
+        "merchant_name": parsed.get("merchant_name"),
+        "date": date_iso,
+        "order_id": parsed.get("order_id"),
+        "grand_total": parsed.get("grand_total"),
+        "subtotal": parsed.get("subtotal"),
+        "tax": parsed.get("tax"),
+        "tip": parsed.get("tip"),
+        "item_count": len(items) or None,
+        "items": items,
+        "payment_method": parsed.get("payment_method"),
+        "currency": currency,
+        "raw_ref": raw_ref or {"mbox_file": None, "byte_offset": None},
+    }
+
+
+def main():
+    ap = argparse.ArgumentParser()
+    ap.add_argument("eml", nargs="+")
+    ap.add_argument("--out", help="directory for per-file .json outputs")
+    args = ap.parse_args()
+    for p in args.eml:
+        ref = None
+        sidecar = p + ".ref.json"
+        if os.path.exists(sidecar):
+            with open(sidecar) as f:
+                rr = json.load(f)
+            ref = {"mbox_file": rr.get("mbox_file"), "byte_offset": rr.get("byte_offset")}
+        res = parse(p, raw_ref=ref)
+        text = json.dumps(res, indent=2, ensure_ascii=False)
+        if args.out:
+            os.makedirs(args.out, exist_ok=True)
+            name = os.path.splitext(os.path.basename(p))[0] + ".json"
+            with open(os.path.join(args.out, name), "w") as f:
+                f.write(text + "\n")
+            print(f"wrote {name}", file=sys.stderr)
+        else:
+            print(text)
+
+
+if __name__ == "__main__":
+    main()
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_travel_housing.py b/infra/email_receipt_inbox/lambdas/parsers/parse_travel_housing.py
new file mode 100644
index 000000000..99ff2e99d
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_travel_housing.py
@@ -0,0 +1,336 @@
+#!/usr/bin/env python3
+"""Prototype parser for the travel-housing email group.
+
+Domains: airbnb.com, hellolanding.com, tesla.com
+
+Handles (with real dollar data in the email body):
+  - Airbnb "Your receipt from Airbnb" (2022-2026, format stable)
+  - Airbnb 2026 "Confirmed: ... here's your Airbnb receipt" trip confirmations
+  - Tesla Shop order confirmations ("Your Tesla Shop Order is Confirmed")
+  - Landing reservation receipts ("Your Landing reservation receipt")
+
+Recognizes but cannot fully extract (no amounts in email body):
+  - Tesla service invoices/estimates (amounts live behind a partner-site link)
+  - Tesla Premium Connectivity subscription notices (no amount shown)
+  - Tesla vehicle order agreements (amounts in PDF attachment; PDF parsing
+    out of scope for this stdlib prototype -- attachment is noted in output)
+  - Landing booking confirmations (no amounts)
+
+Usage: python3 parse_travel-housing.py FILE.eml [--mbox-file X --byte-offset N]
+Emits one JSON object on stdout matching the receipts-online summary shape.
+"""
+
+import argparse
+import json
+import re
+import sys
+from email import policy
+from email.parser import BytesParser
+from email.utils import parseaddr, parsedate_to_datetime
+from html.parser import HTMLParser
+
+MONEY = r"-?\$[\d,]+(?:\.\d{2})?"
+
+
+def money(s):
+    if s is None:
+        return None
+    m = re.search(r"(-?)\$([\d,]+(?:\.\d{2})?)", s)
+    if not m:
+        return None
+    v = float(m.group(2).replace(",", ""))
+    return -v if m.group(1) else v
+
+
+class TextExtract(HTMLParser):
+    """HTML -> line-oriented text. Block-level tags become newlines."""
+
+    BLOCK = {"br", "tr", "p", "div", "td", "th", "h1", "h2", "h3", "h4",
+             "li", "table", "hr"}
+
+    def __init__(self):
+        super().__init__(convert_charrefs=True)
+        self.chunks = []
+        self.skip = 0
+
+    def handle_starttag(self, tag, attrs):
+        if tag in ("style", "script", "head", "title"):
+            self.skip += 1
+        if tag in self.BLOCK:
+            self.chunks.append("\n")
+
+    def handle_endtag(self, tag):
+        if tag in ("style", "script", "head", "title"):
+            self.skip = max(0, self.skip - 1)
+
+    def handle_data(self, d):
+        if not self.skip:
+            self.chunks.append(d)
+
+
+def body_lines(msg):
+    part = msg.get_body(preferencelist=("html",))
+    if part is not None:
+        te = TextExtract()
+        te.feed(part.get_content())
+        text = "".join(te.chunks)
+    else:
+        part = msg.get_body(preferencelist=("plain",))
+        text = part.get_content() if part else ""
+    # normalize: soft hyphens / nbsp / zero-width used as spacers in Airbnb 2026
+    text = text.replace("­", "").replace(" ", " ").replace("​", "")
+    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
+    return [ln for ln in lines if ln]
+
+
+def base_record(msg, args):
+    from_addr = parseaddr(msg.get("From", ""))[1]
+    domain = from_addr.split("@")[-1].lower() if "@" in from_addr else None
+    try:
+        date = parsedate_to_datetime(msg.get("Date")).isoformat()
+    except Exception:
+        date = None
+    return {
+        "source": "email",
+        "message_id": msg.get("Message-ID"),
+        "sender_domain": domain,
+        "merchant_name": None,
+        "date": date,
+        "order_id": None,
+        "grand_total": None,
+        "subtotal": None,
+        "tax": None,
+        "tip": None,
+        "item_count": 0,
+        "items": [],
+        "payment_method": {"type": None, "card_last4": None},
+        "currency": "USD",
+        "raw_ref": {"mbox_file": args.mbox_file, "byte_offset": args.byte_offset},
+    }
+
+
+# ---------------------------------------------------------------- Airbnb
+
+def parse_airbnb(rec, lines, subject):
+    rec["merchant_name"] = "Airbnb"
+    text = "\n".join(lines)
+
+    m = re.search(r"Receipt ID:\s*(RC\w+)", text)
+    if m:
+        rec["order_id"] = m.group(1)
+    else:
+        m = re.search(r"Confirmation code:\s*(HM\w+)", text)
+        if m:
+            rec["order_id"] = m.group(1)
+
+    # receipt-issued date on "Receipt ID: X · Month D, YYYY" line
+    m = re.search(r"Receipt ID:\s*RC\w+\s*[·•-]\s*([A-Z][a-z]+ \d{1,2}, \d{4})", text)
+    if m:
+        try:
+            from datetime import datetime
+            rec["date"] = datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
+        except ValueError:
+            pass
+
+    # price breakdown: label line followed by money-only line, until Total
+    try:
+        i = next(k for k, ln in enumerate(lines)
+                 if ln.lower().startswith("price breakdown"))
+    except StopIteration:
+        return rec
+    items = []
+    label = None
+    j = i + 1
+    while j < len(lines):
+        ln = lines[j]
+        tm = re.match(r"^Total \(([A-Z]{3})\)$", ln)
+        if tm:
+            rec["currency"] = tm.group(1)
+            if j + 1 < len(lines):
+                rec["grand_total"] = money(lines[j + 1])
+            break
+        if re.fullmatch(MONEY, ln):
+            if label:
+                amt = money(ln)
+                qty, unit = 1, amt
+                qm = re.match(r"^(%s) x (\d+) nights?$" % MONEY, label)
+                if qm:
+                    unit = money(qm.group(1))
+                    qty = int(qm.group(2))
+                items.append({"description": label, "quantity": qty,
+                              "unit_price": unit, "total": amt})
+                label = None
+        else:
+            label = ln
+        j += 1
+
+    # tax line -> tax field; everything is kept as items too
+    for it in items:
+        if re.search(r"tax", it["description"], re.I):
+            rec["tax"] = it["total"]
+    rec["items"] = items
+    rec["item_count"] = len(items)
+    if rec["grand_total"] is not None and rec["tax"] is not None:
+        rec["subtotal"] = round(rec["grand_total"] - rec["tax"], 2)
+
+    # payment block: heading "Payment" (or "Payment schedule" for installment
+    # plans) then a method line within the next few lines
+    for k, ln in enumerate(lines):
+        if ln in ("Payment", "Payment schedule"):
+            for meth in lines[k + 1:k + 5]:
+                cm = re.match(r"^([A-Za-z ]+?)\s*[•·]{2,}\s*(\d{4})$", meth)
+                if cm:
+                    rec["payment_method"] = {"type": cm.group(1).strip().lower(),
+                                             "card_last4": cm.group(2)}
+                    break
+                wm = re.search(r"apple pay|google pay|paypal|klarna", meth, re.I)
+                if wm:
+                    rec["payment_method"] = {"type": wm.group(0).lower(),
+                                             "card_last4": None}
+                    break
+            break
+    return rec
+
+
+# ---------------------------------------------------------------- Tesla
+
+def parse_tesla(rec, lines, subject, msg):
+    rec["merchant_name"] = "Tesla"
+    text = "\n".join(lines)
+
+    m = re.search(r"Order\s+#?([A-Z0-9]{8,12})\s+is confirmed", text, re.I)
+    if m:
+        rec["order_id"] = m.group(1)
+    else:
+        m = re.search(r"\bRN\d{9}\b", subject + " " + text)
+        if m:
+            rec["order_id"] = m.group(0)
+
+    # Shop order summary
+    if "Order Summary" in text:
+        try:
+            i = next(k for k, ln in enumerate(lines) if ln == "Order Summary")
+        except StopIteration:
+            i = None
+        if i is not None:
+            items, pending = [], []
+            j = i + 1
+            while j < len(lines):
+                ln = lines[j]
+                if ln == "Subtotal":
+                    break
+                qm = re.match(r"^Quantity:\s*(\d+)$", ln)
+                if qm:
+                    pending.append(("qty", int(qm.group(1))))
+                elif re.fullmatch(MONEY, ln):
+                    desc = next((p[1] for p in pending if p[0] == "desc"), None)
+                    qty = next((p[1] for p in pending if p[0] == "qty"), 1)
+                    amt = money(ln)
+                    if desc:
+                        items.append({"description": desc, "quantity": qty,
+                                      "unit_price": round(amt / qty, 2) if qty else amt,
+                                      "total": amt})
+                    pending = []
+                else:
+                    # description lines (may be multi-line, e.g. "Wall Connector"/"24' Cable")
+                    if pending and pending[0][0] == "desc":
+                        pending[0] = ("desc", pending[0][1] + " - " + ln)
+                    else:
+                        pending.insert(0, ("desc", ln))
+                j += 1
+            rec["items"] = items
+            rec["item_count"] = len(items)
+            for k in range(j, min(j + 12, len(lines))):
+                if lines[k] == "Subtotal" and k + 1 < len(lines):
+                    rec["subtotal"] = money(lines[k + 1])
+                if lines[k] == "Tax" and k + 1 < len(lines):
+                    rec["tax"] = money(lines[k + 1])
+                if lines[k] == "Total" and k + 1 < len(lines):
+                    rec["grand_total"] = money(lines[k + 1])
+
+    # note non-extractable categories honestly
+    if re.search(r"Service (Invoice|Estimate)", subject, re.I):
+        rec["_note"] = ("Tesla service invoice/estimate: amounts are behind a "
+                        "partner-site link, not in the email body")
+    if re.search(r"Subscribed to", subject, re.I):
+        rec["_note"] = "subscription notice; no amount present in email"
+    pdfs = [p.get_filename() for p in msg.walk()
+            if p.get_content_type() == "application/pdf"]
+    if pdfs:
+        rec["_note"] = f"amounts likely in PDF attachment(s): {pdfs}"
+    return rec
+
+
+# ---------------------------------------------------------------- Landing
+
+def parse_landing(rec, lines, subject):
+    rec["merchant_name"] = "Landing"
+    text = "\n".join(lines)
+    m = re.search(r"Property & Unit:\s*(.+)", text)
+    if m:
+        rec["order_id"] = m.group(1).strip()  # no booking number in receipt email
+    try:
+        i = next(k for k, ln in enumerate(lines) if ln == "Description")
+    except StopIteration:
+        return rec
+    items = []
+    label = None
+    j = i + 1
+    while j < len(lines):
+        ln = lines[j]
+        if ln.startswith("Total"):
+            if j + 1 < len(lines):
+                rec["grand_total"] = money(lines[j + 1]) or money(ln)
+            else:
+                rec["grand_total"] = money(ln)
+            break
+        if re.fullmatch(MONEY, ln):
+            if label:
+                items.append({"description": label, "quantity": 1,
+                              "unit_price": money(ln), "total": money(ln)})
+                label = None
+        elif ln != "Amount":
+            label = ln
+        j += 1
+    rec["items"] = items
+    rec["item_count"] = len(items)
+    # Landing receipts have no tax line; surcharge implies card payment
+    if any("credit card surcharge" in it["description"].lower() for it in items):
+        rec["payment_method"] = {"type": "credit card", "card_last4": None}
+    if rec["grand_total"] is not None:
+        rec["subtotal"] = rec["grand_total"]
+    return rec
+
+
+# ---------------------------------------------------------------- main
+
+def main():
+    ap = argparse.ArgumentParser()
+    ap.add_argument("eml")
+    ap.add_argument("--mbox-file", default=None)
+    ap.add_argument("--byte-offset", type=int, default=None)
+    args = ap.parse_args()
+
+    with open(args.eml, "rb") as f:
+        msg = BytesParser(policy=policy.default).parse(f)
+
+    rec = base_record(msg, args)
+    subject = msg.get("Subject", "") or ""
+    lines = body_lines(msg)
+    dom = rec["sender_domain"] or ""
+
+    if "airbnb" in dom:
+        rec = parse_airbnb(rec, lines, subject)
+    elif "tesla" in dom:
+        rec = parse_tesla(rec, lines, subject, msg)
+    elif "landing" in dom or "hellolanding" in dom:
+        rec = parse_landing(rec, lines, subject)
+    else:
+        rec["_note"] = f"unrecognized domain {dom}"
+
+    json.dump(rec, sys.stdout, indent=2)
+    print()
+
+
+if __name__ == "__main__":
+    main()
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_uber.py b/infra/email_receipt_inbox/lambdas/parsers/parse_uber.py
new file mode 100644
index 000000000..72d34799d
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_uber.py
@@ -0,0 +1,341 @@
+#!/usr/bin/env python3
+"""Prototype parser for Uber / Uber Eats receipt emails (sender domain uber.com).
+
+Usage: python3 parse_uber.py <path.eml> [more.eml ...]
+Emits one JSON object per input file (to stdout, or use --outdir DIR to write
+<stem>.json files).
+
+Email types handled:
+  - "Your <day> <time> trip with Uber"        -> ride receipt / trip summary
+  - "Your <day> <time> order with Uber Eats"  -> Eats order receipt
+  - "Uber One payment confirmation"           -> subscription charge
+
+Known format drift:
+  - 2017-era rides: "Your Fare ... CHARGED $X Personal •••• 1234" layout.
+  - 2020-2023 rides: "Total $X ... Amount Charged <method> - 1234" or
+    "Payments <method> <date> $X" layout (payment info present).
+  - 2024+ rides: email is explicitly "not a payment receipt" (trip summary);
+    no payment method / card last4 present.
+  - Eats orders: total + card last4 present, but NO itemization, subtotal,
+    tax or tip (full receipt lives behind a link). Currency can be non-USD
+    (e.g. CRC while traveling).
+"""
+import email
+import email.policy
+import html as html_mod
+import json
+import os
+import re
+import sys
+from datetime import datetime
+from html.parser import HTMLParser
+
+INDEX_JSONL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
+                           "..", "data", "index.jsonl")
+
+BLOCK_TAGS = {"p", "div", "tr", "td", "th", "table", "br", "li", "h1", "h2",
+              "h3", "h4", "ul", "ol"}
+
+
+class _TextExtractor(HTMLParser):
+    def __init__(self):
+        super().__init__(convert_charrefs=True)
+        self.parts = []
+        self._skip = 0
+
+    def handle_starttag(self, tag, attrs):
+        if tag in ("style", "script"):
+            self._skip += 1
+        elif tag in BLOCK_TAGS:
+            self.parts.append("\n")
+
+    def handle_endtag(self, tag):
+        if tag in ("style", "script") and self._skip:
+            self._skip -= 1
+        elif tag in BLOCK_TAGS:
+            self.parts.append("\n")
+
+    def handle_data(self, data):
+        if not self._skip:
+            self.parts.append(data)
+
+    def text(self):
+        t = "".join(self.parts)
+        t = t.replace("\xa0", " ")
+        t = re.sub(r"[ \t]+", " ", t)
+        t = re.sub(r" ?\n ?", "\n", t)
+        t = re.sub(r"\n{2,}", "\n", t)
+        return t.strip()
+
+
+def html_to_text(h):
+    p = _TextExtractor()
+    p.feed(h)
+    return p.text()
+
+
+MONEY = r"(?:(?P<cur>\$|USD|CRC|EUR|€|£|MXN|CAD)\s?)(?P<amt>[\d][\d.,]*)"
+
+
+def _to_float(amt, currency):
+    amt = amt.strip().rstrip(".")
+    if currency == "CRC" and amt.count(",") and amt.count("."):
+        amt = amt.replace(",", "")  # CRC 20,844.90 style
+    else:
+        amt = amt.replace(",", "")
+    try:
+        return float(amt)
+    except ValueError:
+        return None
+
+
+def _currency_name(sym):
+    return {"$": "USD", "€": "EUR", "£": "GBP"}.get(sym, sym)
+
+
+def find_money(text, label_pattern):
+    """Find first money amount following a label regex. Returns (amount, currency)."""
+    m = re.search(label_pattern + r"[^\d$€£A-Z]{0,20}" + MONEY, text,
+                  re.I)
+    if not m:
+        return None, None
+    return _to_float(m.group("amt"), m.group("cur")), _currency_name(m.group("cur"))
+
+
+def parse_body_date(text, fallback_header):
+    m = re.search(r"\b(January|February|March|April|May|June|July|August|"
+                  r"September|October|November|December) (\d{1,2}), (\d{4})",
+                  text)
+    if m:
+        try:
+            return datetime.strptime(
+                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y"
+            ).date().isoformat()
+        except ValueError:
+            pass
+    if fallback_header:
+        try:
+            return email.utils.parsedate_to_datetime(
+                fallback_header).date().isoformat()
+        except Exception:
+            pass
+    return None
+
+
+CARD_PAT = re.compile(
+    r"(?P<method>Apple Pay|Apple Card|Google Pay|Visa|Mastercard|MasterCard|"
+    r"MC|Amex|American Express|Discover|Personal|Business)"
+    r"[^\d\n]{0,25}?(?:•+ ?|[-–] ?|\*+ ?)(?P<last4>\d{4})")
+
+
+def find_payment(text):
+    m = CARD_PAT.search(text)
+    if m:
+        method = m.group("method")
+        ptype = "card"
+        if "Apple Pay" in method or "Google Pay" in method:
+            ptype = "wallet"
+        return {"type": ptype, "card_last4": m.group("last4"),
+                "method": method}
+    if re.search(r"Payments\s*\n?Voucher", text, re.I) or \
+       re.search(r"Voucher:", text):
+        return {"type": "voucher", "card_last4": None, "method": "Voucher"}
+    return None
+
+
+def classify(subject):
+    s = (subject or "").lower()
+    if "trip with uber" in s:
+        return "ride"
+    if "order with uber eats" in s:
+        return "eats"
+    if "uber one payment" in s:
+        return "uber_one"
+    return "unknown"
+
+
+RIDE_FEE_SKIP = re.compile(
+    r"^(Total|Subtotal|Trip fare|Trip Fare|Amount Charged|CHARGED|"
+    r"Payments|Your Fare)\b", re.I)
+
+
+def parse_ride(text):
+    out = {}
+    total, cur = find_money(text, r"\bTotal\b")
+    if total is None:
+        total, cur = find_money(text, r"\bCHARGED\b")
+    out["grand_total"] = total
+    out["currency"] = cur
+    sub, _ = find_money(text, r"\bSubtotal\b")
+    out["subtotal"] = sub
+    tip, _ = find_money(text, r"\bTip(?:\s+amount)?\b")
+    out["tip"] = tip
+    tax, _ = find_money(text, r"\b(?:Sales )?Tax\b")
+    out["tax"] = tax
+    # ride description: e.g. "UberX\n7.64 miles | 12 min"
+    m = re.search(r"(UberX|UberXL|Comfort|Uber Green|Black SUV|Black|Pool|"
+                  r"UberPool|Express Pool|uberX|WAV|Connect)\s*\n?"
+                  r"([\d.]+ (?:miles|kilometers) \| [\dh ]+min)?", text)
+    desc = None
+    if m:
+        desc = m.group(1)
+        if m.group(2):
+            desc += " " + m.group(2)
+    items = []
+    if desc:
+        items.append({"description": "Ride: " + desc, "quantity": 1,
+                      "unit_price": None, "total": total})
+    out["items"] = items
+    out["merchant_name"] = "Uber"
+    return out
+
+
+def parse_eats(text):
+    out = {}
+    total, cur = find_money(text, r"\bTotal\b")
+    out["grand_total"] = total
+    out["currency"] = cur
+    out["subtotal"] = None   # not present in Eats emails
+    out["tax"] = None
+    out["tip"] = None
+    m = re.search(r"Here's your receipt for (.+?)\.(?:\s|$)", text)
+    restaurant = m.group(1).strip() if m else None
+    if not restaurant:
+        m = re.search(r"You ordered from (.+)", text)
+        restaurant = m.group(1).strip() if m else None
+    items = []
+    if restaurant:
+        items.append({"description": f"Uber Eats order from {restaurant}",
+                      "quantity": 1, "unit_price": None, "total": total})
+    out["items"] = items
+    out["merchant_name"] = "Uber Eats" + (f" ({restaurant})" if restaurant else "")
+    return out
+
+
+def parse_uber_one(text):
+    out = {}
+    total, cur = find_money(text, r"Total charged")
+    out["grand_total"] = total
+    out["currency"] = cur
+    out["subtotal"] = None
+    out["tax"] = None
+    out["tip"] = None
+    out["items"] = [{"description": "Uber One membership (monthly)",
+                     "quantity": 1, "unit_price": None, "total": total}]
+    out["merchant_name"] = "Uber One"
+    return out
+
+
+def load_raw_ref(message_id):
+    if not message_id or not os.path.exists(INDEX_JSONL):
+        return None
+    try:
+        with open(INDEX_JSONL) as f:
+            for line in f:
+                if message_id in line:
+                    r = json.loads(line)
+                    if (r.get("message_id") or "").strip("<> ") == message_id:
+                        return {"mbox_file": r["mbox_file"],
+                                "byte_offset": r["byte_offset"]}
+    except OSError:
+        return None
+    return None
+
+
+def parse_eml(path):
+    with open(path, "rb") as f:
+        raw = f.read()
+    msg = email.message_from_bytes(raw, policy=email.policy.default)
+    subject = msg.get("Subject", "")
+    message_id = (msg.get("Message-Id") or "").strip("<> ")
+    from_addr = email.utils.parseaddr(msg.get("From", ""))[1]
+    sender_domain = from_addr.split("@")[-1] if "@" in from_addr else None
+
+    # get HTML (or plain) body
+    body_html = None
+    body_plain = None
+    for part in msg.walk():
+        ct = part.get_content_type()
+        if ct == "text/html" and body_html is None:
+            body_html = part.get_content()
+        elif ct == "text/plain" and body_plain is None:
+            body_plain = part.get_content()
+    text = html_to_text(body_html) if body_html else (body_plain or "")
+    text = html_mod.unescape(text)
+
+    kind = classify(subject)
+    if kind == "ride":
+        fields = parse_ride(text)
+    elif kind == "eats":
+        fields = parse_eats(text)
+    elif kind == "uber_one":
+        fields = parse_uber_one(text)
+    else:
+        fields = {"grand_total": None, "subtotal": None, "tax": None,
+                  "tip": None, "items": [], "merchant_name": "Uber",
+                  "currency": None}
+
+    order_id = None
+    m = re.search(r"\bxid([0-9a-f-]{20,})", text)
+    if m:
+        order_id = m.group(1)
+
+    payment = find_payment(text)
+    # 2024+ ride "trip summary" emails explicitly carry no payment info
+    is_trip_summary = "This is not a payment receipt" in text
+
+    result = {
+        "source": "email",
+        "message_id": message_id,
+        "sender_domain": sender_domain,
+        "merchant_name": fields["merchant_name"],
+        "date": parse_body_date(text, msg.get("Date")),
+        "order_id": order_id,
+        "grand_total": fields["grand_total"],
+        "subtotal": fields["subtotal"],
+        "tax": fields["tax"],
+        "tip": fields["tip"],
+        "item_count": len(fields["items"]),
+        "items": fields["items"],
+        "payment_method": ({"type": payment["type"],
+                            "card_last4": payment["card_last4"]}
+                           if payment else
+                           {"type": None, "card_last4": None}),
+        "currency": fields["currency"],
+        "raw_ref": load_raw_ref(message_id),
+        # extra context (non-schema, prefixed with _)
+        "_email_kind": kind,
+        "_trip_summary_no_payment": is_trip_summary,
+    }
+    return result
+
+
+def main(argv):
+    outdir = None
+    args = []
+    it = iter(argv)
+    for a in it:
+        if a == "--outdir":
+            outdir = next(it)
+        else:
+            args.append(a)
+    if not args:
+        print("usage: parse_uber.py [--outdir DIR] file.eml [...]",
+              file=sys.stderr)
+        return 1
+    for p in args:
+        res = parse_eml(p)
+        js = json.dumps(res, indent=2, ensure_ascii=False)
+        if outdir:
+            os.makedirs(outdir, exist_ok=True)
+            stem = os.path.splitext(os.path.basename(p))[0]
+            with open(os.path.join(outdir, stem + ".json"), "w") as f:
+                f.write(js + "\n")
+            print(f"wrote {stem}.json")
+        else:
+            print(js)
+    return 0
+
+
+if __name__ == "__main__":
+    sys.exit(main(sys.argv[1:]))
diff --git a/infra/email_receipt_inbox/lambdas/parsers/parse_venmo.py b/infra/email_receipt_inbox/lambdas/parsers/parse_venmo.py
new file mode 100644
index 000000000..aac31b277
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/parsers/parse_venmo.py
@@ -0,0 +1,300 @@
+#!/usr/bin/env python3
+"""Prototype parser for Venmo notification emails -> receipt-schema JSON.
+
+Handles three format eras:
+  A. Classic P2P template (2016 - early 2025):
+     "You paid NAME" / "NAME paid you" / "You charged NAME" + note +
+     "Transfer Date and Amount: Mon DD, YYYY TZ . - $X.XX" + "Payment ID: N"
+  B. New P2P template (mid 2025+):
+     hero card "You paid NAME $ X . XX" + note + "Transaction details"
+     table (Date / Transaction ID / Payment Method / Sent from).
+  C. Merchant purchase receipts ("Receipt from DoorDash - $X.XX"):
+     Venmo used as checkout wallet at a merchant; body has merchant name,
+     purchase datetime (no year in old ones), funding source, total.
+
+Emits JSON aligned with the receipts-online summary shape.
+Usage: parse_venmo.py file.eml [file2.eml ...]  (prints one JSON per file)
+"""
+import sys, os, re, json, email, email.policy
+from datetime import datetime
+from html.parser import HTMLParser
+
+BLOCK_TAGS = {"p", "div", "tr", "td", "th", "table", "br", "li", "h1", "h2",
+              "h3", "h4", "center", "blockquote"}
+
+
+class TextExtractor(HTMLParser):
+    """Flatten HTML to text, newline at block boundaries, skip style/script."""
+
+    def __init__(self):
+        super().__init__(convert_charrefs=True)
+        self.chunks = []
+        self._skip = 0
+
+    def handle_starttag(self, tag, attrs):
+        if tag in ("style", "script", "head", "title"):
+            self._skip += 1
+        elif tag in BLOCK_TAGS:
+            self.chunks.append("\n")
+
+    def handle_endtag(self, tag):
+        if tag in ("style", "script", "head", "title") and self._skip:
+            self._skip -= 1
+        elif tag in BLOCK_TAGS:
+            self.chunks.append("\n")
+
+    def handle_data(self, data):
+        if not self._skip:
+            self.chunks.append(data)
+
+    def text(self):
+        raw = "".join(self.chunks)
+        raw = raw.replace("\xa0", " ").replace(" ", " ")
+        lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw.split("\n")]
+        return [ln for ln in lines if ln]
+
+
+def get_html_text(msg):
+    html_body = plain_body = None
+    for part in msg.walk():
+        ct = part.get_content_type()
+        if ct == "text/html" and html_body is None:
+            html_body = part.get_content()
+        elif ct == "text/plain" and plain_body is None:
+            plain_body = part.get_content()
+    if html_body:
+        ex = TextExtractor()
+        ex.feed(html_body)
+        return ex.text()
+    if plain_body:
+        return [ln.strip() for ln in plain_body.splitlines() if ln.strip()]
+    return []
+
+
+AMT = r"\$\s?([\d,]+(?:\s?\.\s?\d{2})?)"
+
+
+def money(s):
+    if s is None:
+        return None
+    return float(re.sub(r"[,\s]", "", s))
+
+
+def parse_date_us(s, fallback_year=None):
+    """'Jun 30, 2025' or 'Nov 12, 2016' -> ISO date."""
+    for fmt in ("%b %d, %Y", "%B %d, %Y"):
+        try:
+            return datetime.strptime(s.strip(), fmt).date().isoformat()
+        except ValueError:
+            pass
+    return None
+
+
+def classify(subject):
+    sl = (subject or "").lower()
+    if sl.startswith("receipt from"):
+        return "merchant_purchase"
+    if re.match(r"you paid .+ \$", sl):
+        return "p2p_sent"
+    if re.search(r" paid you \$", sl):
+        return "p2p_received"
+    if re.search(r"you completed .+ charge request", sl):
+        return "p2p_sent"          # you paid a request someone sent you
+    if re.search(r"completed your .+ charge request", sl):
+        return "p2p_received"      # they paid your request
+    if "you sent money on imessage" in sl:
+        return "p2p_sent"
+    if "accepted your" in sl and "payment on imessage" in sl:
+        return "p2p_sent"
+    return None
+
+
+def extract_payment_method(text):
+    """Return (type, last4) from funding-source sentences."""
+    m = re.search(r"(?:account|checking|savings)\s+ending in\s+\D*(\d{4})",
+                  text, re.I)
+    if m:
+        return "bank_account", m.group(1)
+    m = re.search(r"card\s+ending in\s+\D*(\d{4})", text, re.I)
+    if m:
+        return "card", m.group(1)
+    if re.search(r"charged to your (?:debit|credit) card", text, re.I):
+        return "card", None
+    if re.search(r"venmo balance", text, re.I):
+        return "venmo_balance", None
+    if re.search(r"bank transfer|personal (checking|savings)", text, re.I):
+        return "bank_account", None
+    return None, None
+
+
+def parse_eml(path):
+    with open(path, "rb") as f:
+        msg = email.message_from_binary_file(f, policy=email.policy.default)
+    subject = str(msg.get("subject", "")).strip()
+    kind = classify(subject)
+    lines = get_html_text(msg)
+    text = " ".join(lines)
+    hdr_date = None
+    if msg["date"]:
+        try:
+            hdr_date = email.utils.parsedate_to_datetime(str(msg["date"]))
+        except Exception:
+            pass
+
+    out = {
+        "source": "email",
+        "message_id": str(msg.get("message-id", "")).strip() or None,
+        "sender_domain": "venmo.com",
+        "merchant_name": None,
+        "date": hdr_date.date().isoformat() if hdr_date else None,
+        "order_id": None,
+        "grand_total": None,
+        "subtotal": None,
+        "tax": None,
+        "tip": None,
+        "item_count": 0,
+        "items": [],
+        "payment_method": {"type": None, "card_last4": None},
+        "currency": "USD",
+        "raw_ref": {"mbox_file": None, "byte_offset": None},
+        # extras beyond base schema (P2P semantics)
+        "transaction_kind": kind,          # p2p_sent / p2p_received / merchant_purchase
+        "direction": None,                 # outflow / inflow
+        "counterparty": None,
+        "note": None,
+    }
+    if kind is None:
+        out["error"] = "not a transaction email (marketing/security/statement)"
+        return out
+
+    # ---- counterparty + amount from subject ----
+    m = (re.match(r"You paid (.+?) \$([\d,]+\.?\d*)", subject)
+         or re.match(r"(.+?) paid you \$([\d,]+\.?\d*)", subject)
+         or re.match(r"You completed (.+?)['’]s \$([\d,]+\.?\d*) charge",
+                     subject)
+         or re.match(r"(.+?) completed your \$([\d,]+\.?\d*) charge", subject)
+         or re.match(r"(.+?) accepted your \$([\d,]+\.?\d*) payment", subject))
+    if m:
+        out["counterparty"] = m.group(1).strip()
+        out["grand_total"] = money(m.group(2))
+    m = re.match(r"Receipt from (.+?) - \$([\d,]+\.?\d*)", subject)
+    if m:
+        out["merchant_name"] = m.group(1).strip()
+        out["grand_total"] = money(m.group(2))
+
+    if kind == "merchant_purchase":
+        # date like "Tuesday, October 28 at 09:00PM PDT" (no year) -> header yr
+        m = re.search(r"on \w+day, (\w+ \d{1,2}) at", text)
+        if m and hdr_date:
+            try:
+                d = datetime.strptime(
+                    f"{m.group(1)}, {hdr_date.year}", "%B %d, %Y").date()
+                # purchase may be shortly before email; year boundary guard
+                if hdr_date and d > hdr_date.date():
+                    d = d.replace(year=d.year - 1)
+                out["date"] = d.isoformat()
+            except ValueError:
+                pass
+        if out["grand_total"] is None:
+            m = re.search(r"Total(?: to merchant)?:?\s*" + AMT, text)
+            if not m:
+                m = re.search(r"-\s?" + AMT, text)
+            if m:
+                out["grand_total"] = money(m.group(1))
+    else:
+        # ---- classic template ----
+        m = re.search(r"Transfer Date and Amount:\s*(\w{3} \d{2}, \d{4})", text)
+        if m:
+            out["date"] = parse_date_us(m.group(1)) or out["date"]
+            am = re.search(
+                r"Transfer Date and Amount:.{0,40}?([+-])\s?" + AMT, text)
+            if am:
+                if out["grand_total"] is None:
+                    out["grand_total"] = money(am.group(2))
+                out["direction"] = "outflow" if am.group(1) == "-" else "inflow"
+        # ---- new 2025 template ----
+        m = re.search(r"Transaction details\s+Date\s+(\w{3} \d{1,2}, \d{4})",
+                      text)
+        if m:
+            out["date"] = parse_date_us(m.group(1)) or out["date"]
+        if out["grand_total"] is None:
+            m = re.search(AMT, text)
+            if m:
+                out["grand_total"] = money(m.group(1))
+
+    if out["direction"] is None:
+        out["direction"] = ("inflow" if kind == "p2p_received" else "outflow")
+
+    # ---- payment / transaction id ----
+    m = re.search(r"(?:Payment ID|Transaction ID)\D{0,5}(\d{6,})", text)
+    if m:
+        out["order_id"] = m.group(1)
+
+    # ---- funding source ----
+    ptype, last4 = extract_payment_method(text)
+    if kind == "p2p_received":
+        ptype, last4 = "venmo_balance", None  # credited to balance
+    out["payment_method"] = {"type": ptype, "card_last4": last4}
+
+    # ---- note (memo) ----
+    note = extract_note(lines, subject, kind, out)
+    out["note"] = note
+
+    # P2P merchant_name: use counterparty so reconciliation has a name
+    if out["merchant_name"] is None and out["counterparty"]:
+        out["merchant_name"] = out["counterparty"]
+
+    # single pseudo-item from the note
+    if note or out["grand_total"] is not None:
+        desc = note or subject
+        out["items"] = [{"description": desc, "quantity": 1,
+                         "unit_price": out["grand_total"],
+                         "total": out["grand_total"]}]
+        out["item_count"] = 1
+    return out
+
+
+def extract_note(lines, subject, kind, out):
+    """The memo is the line(s) just before the date/details marker.
+
+    Classic template rendering:
+        You / paid / Claire Rhodes / <note> / Transfer Date and Amount: ...
+    New (2025+) template rendering:
+        You paid Kyle Covelli / $ / 55 / . / 00 / <note> / See transaction
+    """
+    if kind == "merchant_purchase":
+        return None
+    end_idx = None
+    for i, ln in enumerate(lines):
+        if re.match(r"^(Transfer Date and Amount|See transaction)", ln):
+            end_idx = i
+            break
+    if end_idx is None:
+        return None
+    cp = out.get("counterparty") or ""
+    stop = {"you", "paid", "charged", "sent money on imessage",
+            "payment not accepted yet", cp.lower(), subject.lower(),
+            "- venmo", "venmo"}
+    note_parts = []
+    for ln in reversed(lines[max(0, end_idx - 4):end_idx]):
+        low = ln.lower()
+        if low in stop or low.startswith(("you paid", "you charged")) \
+                or re.search(r"paid you", low) or re.search(r"charged", low):
+            break
+        # amount fragments: '$', '55', '.', '00', '- $15.00'
+        if re.fullmatch(r"[+\-·]?\s?\$?\s?[\d,.\s]*", ln):
+            break
+        note_parts.append(ln)
+    note = " ".join(reversed(note_parts)).strip()
+    note = re.sub(r"\$\s?[\d,]+\s?\.\s?\d{2}", "", note).strip()
+    return note or None
+
+
+def main():
+    for path in sys.argv[1:]:
+        result = parse_eml(path)
+        print(json.dumps(result, indent=2, ensure_ascii=False))
+
+
+if __name__ == "__main__":
+    main()
diff --git a/infra/email_receipt_inbox/lambdas/registry.py b/infra/email_receipt_inbox/lambdas/registry.py
new file mode 100644
index 000000000..50af54d85
--- /dev/null
+++ b/infra/email_receipt_inbox/lambdas/registry.py
@@ -0,0 +1,78 @@
+"""Sender-domain -> parser registry for inbound email receipts.
+
+Parsers are stdlib-only modules exposing ``parse(path) -> dict`` (or
+``parse_eml``) that take an RFC-822 .eml file path and return the shared
+receipt schema (dollars as floats, ``grand_total is None`` for non-receipts).
+"""
+from __future__ import annotations
+
+import importlib
+import re
+from typing import Any, Optional
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
+def run_parser(grp: str, eml_path: str) -> Any:
+    _, module_name, entry = GROUPS[grp]
+    mod = importlib.import_module(f"parsers.{module_name}")
+    return getattr(mod, entry)(eml_path)
+
+
+def classify(grp: str, subject: str, parsed: Any) -> str:
+    """-> 'receipt' | 'txn_signal' | 'needs_ocr' | 'non_receipt'."""
+    if grp == "chase-alerts":
+        return "txn_signal"
+    if isinstance(parsed, list):
+        parsed = parsed[0] if parsed else {}
+    parsed = parsed or {}
+    if parsed.get("needs_pdf") or parsed.get("needs_ocr"):
+        return "needs_ocr"
+    gate = SUBJECT_DROP.get(grp)
+    if gate and gate.search(subject or ""):
+        return "non_receipt"
+    if parsed.get("grand_total") is None:
+        return "non_receipt"
+    return "receipt"
```

## Full contents of core files (current state)
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

        # --- SES identity + DKIM + inbound MX on the isolated subdomain
        identity = aws.ses.DomainIdentity(f"{name}-identity", domain=domain,
                                          opts=child)
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
        if raw_retention_days:
            aws.s3.BucketLifecycleConfiguration(
                f"{name}-mail-lifecycle",
                bucket=self.bucket.id,
                rules=[{"id": "expire-raw", "status": "Enabled",
                        "filter": {"prefix": "raw/"},
                        "expiration": {"days": raw_retention_days}}],
                opts=child)
        aws.s3.BucketPolicy(
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
        aws.iam.RolePolicyAttachment(
            f"{name}-parser-logs",
            role=role.name,
            policy_arn=aws.iam.ManagedPolicy.AWS_LAMBDA_BASIC_EXECUTION_ROLE,
            opts=child)
        aws.iam.RolePolicy(
            f"{name}-parser-s3",
            role=role.id,
            policy=self.bucket.arn.apply(lambda arn: pulumi.Output.json_dumps({
                "Version": "2012-10-17",
                "Statement": [
                    {"Effect": "Allow", "Action": ["s3:GetObject"],
                     "Resource": f"{arn}/raw/*"},
                    {"Effect": "Allow", "Action": ["s3:PutObject"],
                     "Resource": f"{arn}/parsed/*"},
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
            opts=child)
        aws.lambda_.Permission(
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
            opts=ResourceOptions(parent=self, depends_on=[self.parser]))

        # --- receipt rule set
        rule_set = aws.ses.ReceiptRuleSet(
            f"{name}-rules", rule_set_name=f"{name}-{stack}", opts=child)
        aws.ses.ReceiptRule(
            f"{name}-store-rule",
            rule_set_name=rule_set.rule_set_name,
            recipients=[self.address],
            enabled=True,
            scan_enabled=True,
            s3_actions=[{
                "bucket_name": self.bucket.bucket,
                "object_key_prefix": "raw/",
                "position": 1,
            }],
            opts=ResourceOptions(parent=self, depends_on=[rule_set]))
        if activate:
            aws.ses.ActiveReceiptRuleSet(
                f"{name}-rules-active",
                rule_set_name=rule_set.rule_set_name,
                opts=ResourceOptions(parent=self, depends_on=[rule_set]))

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
parsed/<messageId>.json:

    {
      "message_id": "...",        # RFC-822 Message-ID (falls back to S3 key)
      "s3_key": "raw/...",
      "from_domain": "doordash.com",
      "original_from": "...",     # unwrapped when the mail arrived via a
                                  # forwarding rule (X-Forwarded-For et al.)
      "subject": "...",
      "group": "doordash" | null,
      "classification": "receipt" | "txn_signal" | "needs_ocr" | "non_receipt"
                        | "unknown_sender" | "parse_error",
      "receipt": {...} | null,    # the parser's raw schema output
      "error": "..." | null
    }

The private reconciliation plane polls parsed/ and ingests idempotently by
message_id. This function deliberately writes derived data only — no DynamoDB
coupling — so re-parses are a matter of re-running over raw/.
"""
from __future__ import annotations

import email
import email.policy
import email.utils
import json
import os
import re
import tempfile
import traceback
import urllib.parse

import boto3

import registry

s3 = boto3.client("s3")


def _from_domain(msg) -> tuple[str, str]:
    """Return (from_addr, domain), preferring the ORIGINAL sender when the
    message was auto-forwarded (iCloud rules keep it in these headers)."""
    for header in ("X-Original-From", "Reply-To", "From"):
        raw = msg.get(header)
        if not raw:
            continue
        addr = email.utils.parseaddr(raw)[1]
        m = re.search(r"@([\w.\-]+)", addr or "")
        if m:
            return addr, m.group(1).lower()
    return "", ""


def lambda_handler(event, _context):
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        out = {"s3_key": key, "group": None, "classification": "unknown_sender",
               "receipt": None, "error": None}
        try:
            raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            out["message_id"] = (msg.get("Message-ID") or key).strip()
            out["subject"] = msg.get("Subject", "")
            from_addr, domain = _from_domain(msg)
            out["from_domain"] = domain
            out["original_from"] = from_addr
            grp = registry.group_for_domain(domain)
            out["group"] = grp
            if grp:
                with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as tmp:
                    tmp.write(raw)
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
        except Exception:
            out["classification"] = "parse_error"
            out["error"] = traceback.format_exc(limit=4)

        dest = "parsed/" + key.split("/", 1)[-1] + ".json"
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
import re
from typing import Any, Optional

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


def run_parser(grp: str, eml_path: str) -> Any:
    _, module_name, entry = GROUPS[grp]
    mod = importlib.import_module(f"parsers.{module_name}")
    return getattr(mod, entry)(eml_path)


def classify(grp: str, subject: str, parsed: Any) -> str:
    """-> 'receipt' | 'txn_signal' | 'needs_ocr' | 'non_receipt'."""
    if grp == "chase-alerts":
        return "txn_signal"
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}
    parsed = parsed or {}
    if parsed.get("needs_pdf") or parsed.get("needs_ocr"):
        return "needs_ocr"
    gate = SUBJECT_DROP.get(grp)
    if gate and gate.search(subject or ""):
        return "non_receipt"
    if parsed.get("grand_total") is None:
        return "non_receipt"
    return "receipt"
```

## Cumulative decision log (all prior rounds)
---
OUTPUT FORMAT (strict):
- If you have NO further findings of ANY severity, your ENTIRE response
  must be exactly this token and nothing else: APPROVED-NO-SUGGESTIONS
- Otherwise: a numbered list. Every finding MUST start with a severity
  tag: [high], [med], [low], or [deadlock]. Then file, what is wrong,
  and a specific suggested fix. No praise, no design restatement.
- [low] = advisory polish; the loop terminates when only [low]/[deadlock]
  remain, and they are recorded for a human. Tag honestly.
