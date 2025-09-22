## NAT egress issue: symptoms and fix

### Symptoms

- ValidateReceipt Lambda timed out calling external APIs (OpenAI) from private subnets.
- Connectivity probe Lambda returned `{ "error": "<urlopen error timed out>" }`.
- CloudWatch logs showed ConnectTimeoutError to `https://dynamodb.us-east-1.amazonaws.com/` before adding the DynamoDB VPC endpoint.

### What was already correct

- Private route table had default route (0.0.0.0/0) to the NAT instance ENI.
- NAT subnet route table had default route to the Internet Gateway (IGW).
- Lambda security group: egress 0.0.0.0/0.
- NAT security group: egress 0.0.0.0/0; ingress from private CIDRs / Lambda SG.
- NACLs: allow-all inbound/outbound.
- VPC DNS support and hostnames enabled.

### Root cause

- The NAT instance OS was not performing IP forwarding and NAT masquerading. Even with routing and SGs correct, packets were not being forwarded/NATed, causing timeouts.

### Fixes applied

1. Add DynamoDB Gateway VPC Endpoint to private and public route tables so DynamoDB calls do not require internet.
2. Open NAT SG to Lambda SG (and private CIDRs) for inbound.
3. Update `NatEgress` user data to enable forwarding and NAT persistently:

   - `net.ipv4.ip_forward = 1` (persist + apply)
   - `iptables -t nat -A POSTROUTING -o <egress> -j MASQUERADE`
   - Accept FORWARD (including RELATED,ESTABLISHED)
   - Save rules and enable iptables service

   File: `infra/chroma/nat_egress.py` (NAT instance `user_data`)

4. Set `user_data_replace_on_change=True` so changes are applied on update.
5. Rebooted the NAT instance to refresh forwarding/iptables.

### Verification

- Invoked connectivity probe Lambda from private subnets:
  - `http://checkip.amazonaws.com` → returned public IP (success)
  - `https://api.openai.com/v1/models` → returned status (or 401 without key)
- ValidateReceipt completed without network timeouts.

### Notes / alternatives

- Keep NAT instance always on (low cost ~ $7–8/mo + egress for t4g.nano) for reliability.
- For zero-maintenance, use a managed NAT Gateway (~$33/mo + $/GB) instead of an instance.
- If a Lambda does not need VPC resources, removing VPC attachment gives default internet access without NAT.
