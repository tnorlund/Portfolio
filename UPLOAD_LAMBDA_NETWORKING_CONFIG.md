# Upload Lambda Networking Configuration

## Current Setup: `upload-images-process-ocr-image-dev`

### Network Topology
```
Internet
    ↓
NAT Instance (i-0fd755... in public subnet)
    ↓
Private Subnets (10.0.101.0/24, 10.0.102.0/24)
    ↓
Lambda + ENI
    ↓
    ├─→ SQS (via VPC Interface Endpoint) ✅
    ├─→ EFS (via EFS mount targets) ⚠️ Currently disabled
    ├─→ OpenAI APIs (via NAT to internet) ✅
    ├─→ Google Places API (via NAT to internet) ✅
    ├─→ S3 (via Gateway Endpoint) ✅
    └─→ DynamoDB (via Gateway Endpoint) ✅
```

---

## 1. SQS Queue Access (`upload-images-dev-ocr-results-queue`)

### Event Source Mapping
The Lambda automatically polls the queue via AWS's event source mapping.

**How it works:**
1. Event source mapping is configured in `infra/upload_images/infra.py`:
   ```python
   aws.lambda_.EventSourceMapping(
       f"{name}-ocr-results-mapping",
       event_source_arn=self.ocr_results_queue.arn,
       function_name=process_ocr_lambda.name,
       batch_size=10,
       enabled=True,
   )
   ```

2. **AWS handles the polling** - Lambda service polls SQS internally
3. Lambda ENI allows access to SQS via VPC endpoint

### VPC Endpoint for SQS
Located in `infra/__main__.py`:
```python
sqs_interface_endpoint = aws.ec2.VpcEndpoint(
    f"sqs-interface-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    service_name=f"com.amazonaws.{aws.config.region}.sqs",
    vpc_endpoint_type="Interface",
    subnet_ids=Output.all(public_vpc.public_subnet_ids, nat.private_subnet_ids).apply(
        lambda args: args[0] + [args[1][0]]
    ),  # Public subnets + first private subnet only
    security_group_ids=[security.sg_vpce_id],
    private_dns_enabled=True,
)
```

**What this does:**
- Creates a private network interface for SQS service
- Lambda reaches SQS via private AWS network (no internet required)
- **Cost**: $7.20/month** (always running)

### Security Groups
1. **Lambda SG** (`sg-025b2a030a20e6037`): Allows all egress
2. **VPC Endpoint SG** (`sg-0d1a96dbf95ed7af6`): Allows inbound from Lambda SG

**Result**: Lambda can poll SQS from private subnet without internet!

---

## 2. EFS Access

### Current Status: **DISABLED** ⚠️

The EFS mount is commented out in `infra/upload_images/infra.py`:
```python
# Temporarily disable EFS for upload lambda (causes initialization hangs)
# "file_system_config": {
#     "arn": efs_access_point_arn,
#     "local_mount_path": "/mnt/chroma",
# } if efs_access_point_arn else None,
```

### How to Enable EFS Access

**Step 1: Re-enable the file system config**
Uncomment the `file_system_config` in `infra/upload_images/infra.py`:

```python
"file_system_config": {
    "arn": efs_access_point_arn,
    "local_mount_path": "/mnt/chroma",
} if efs_access_point_arn else None,
```

**Step 2: Deploy with Pulumi**
```bash
cd infra && pulumi up
```

**Step 3: Lambda will create ENI for EFS**
When Lambda cold starts with EFS configured:
1. Lambda service creates ENI in subnet `subnet-02f1818a1e813b126` (first private subnet)
2. ENI gets private IP (e.g., `10.0.101.x`)
3. Lambda mounts EFS via mount target at `/mnt/chroma`

### EFS Mount Targets
EFS has mount targets in multiple subnets:
- Public subnet 1 (AZ `us-east-1a`)
- Public subnet 2 (AZ `us-east-1b`)
- **Private subnet 1 (AZ `us-east-1f`)** ← Lambda uses this

**Note**: Both private subnets are in same AZ (`us-east-1f`), so only one mount target exists for them.

### Why EFS Was Disabled
During testing, EFS mount during Lambda initialization was taking >10 seconds, causing:
1. Init timeout errors
2. Messages getting stuck in SQS "in-flight" state
3. Lambda not processing messages

**The fix**: Disable EFS, use S3-only mode (`CHROMADB_STORAGE_MODE: "s3"`)

---

## 3. Internet Access (OpenAI, Google Places)

### NAT Instance
- **Type**: t4g.nano (ARM-based, cheapest option)
- **Cost**: $3.07/month
- **Purpose**: Provides outbound internet access from private subnets

**How it works:**
1. Lambda in private subnet wants to reach `api.openai.com`
2. Traffic flows: Lambda ENI → Route Table → NAT Instance ENI → Internet Gateway → Internet
3. Response flows back through same path

### Security Groups
- Lambda SG: Allows all egress (to go through NAT)
- NAT instance SG: Allows all traffic (it's in public subnet)

**Result**: Lambda can reach OpenAI and Google Places APIs! ✅

---

## 4. S3 Access (ChromaDB snapshots/deltas)

### VPC Gateway Endpoint
Located in `infra/__main__.py`:
```python
s3_gateway_endpoint = aws.ec2.VpcEndpoint(
    f"s3-gateway-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    service_name=f"com.amazonaws.{aws.config.region}.s3",
    vpc_endpoint_type="Gateway",
    route_table_ids=[public_vpc.public_route_table_id, nat.private_rt.id],
)
```

**What this does:**
- Adds route to S3 via AWS private network
- **No data transfer cost** (same as internet would be)
- Faster than public internet (dedicated AWS backbone)

---

## 5. DynamoDB Access

### VPC Gateway Endpoint
```python
dynamodb_gateway_endpoint = aws.ec2.VpcEndpoint(
    f"dynamodb-gateway-{pulumi.get_stack()}",
    vpc_id=public_vpc.vpc_id,
    service_name=f"com.amazonaws.{aws.config.region}.dynamodb",
    vpc_endpoint_type="Gateway",
    route_table_ids=[public_vpc.public_route_table_id, nat.private_rt.id],
)
```

**What this does:
- Adds route to DynamoDB via AWS private network
- **No cost** (gateway endpoints are free)
- Faster than public internet

---

## Current Networking Configuration Summary

| Service | Access Method | Endpoint | Cost | Status |
|---------|--------------|----------|------|--------|
| **SQS** | VPC Interface Endpoint | vpce-0145e81bc1cd8163d | $7.20/month | ✅ Working |
| **EFS** | EFS Mount Target | EFS file system | Storage only | ⚠️ Disabled |
| **OpenAI** | NAT Instance | i-0fd755... | $3.07/month | ✅ Working |
| **Google Places** | NAT Instance | i-0fd755... | Included | ✅ Working |
| **S3** | VPC Gateway Endpoint | Gateway | Free | ✅ Working |
| **DynamoDB** | VPC Gateway Endpoint | Gateway | Free | ✅ Working |

---

## To Re-Enable EFS

**Why disable it?** 
- EFS mount during cold start takes >10 seconds
- This exceeds Lambda's init time limit
- Messages get stuck in SQS "in-flight" state

**Potential solutions:**

1. **Use S3-only mode** (current): 
   - Download ChromaDB from S3 on each invocation
   - No EFS needed
   - Works, but slower

2. **Re-enable EFS with longer timeout**:
   ```python
   "timeout": 900,  # 15 minutes
   ```
   - Allows time for EFS initialization
   - But adds cold start delay

3. **Keep Lambda warm** (provisioned concurrency):
   - Cost: ~$10/month per function
   - Prevents cold starts
   - EFS mount stays active

4. **Use EFS with local copy optimization** (what compaction lambda does):
   - Mount EFS
   - Copy ChromaDB from EFS to `/tmp` (local storage)
   - Use local copy (fast I/O)
   - Copy back to EFS after updates
   - This is what `enhanced_compaction_handler.py` does

**Recommendation**: 
- Keep EFS disabled for now (S3-only mode works)
- If performance becomes critical, implement option 4 (local copy optimization)

---

## Troubleshooting

### Lambda not processing messages?
1. Check if ENI exists: `aws ec2 describe-network-interfaces --filters ...`
2. Check event source mapping state: `aws lambda get-event-source-mapping ...`
3. Check for init timeouts in CloudWatch logs
4. Try manual invocation: `aws lambda invoke ...`

### Messages stuck "in-flight"?
1. Lambda timed out during processing
2. Wait for visibility timeout to expire (usually 60 seconds)
3. Or manually reset: `aws sqs change-message-visibility ...`

### EFS connection timeout?
1. Check EFS mount targets are in same subnets as Lambda
2. Check security groups allow NFS traffic
3. Check EFS access point ARN is correct
4. Consider increasing Lambda timeout
5. Or disable EFS and use S3-only mode

