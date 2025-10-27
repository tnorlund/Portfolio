# Upload Process Networking Architecture

## Overview

This document describes the networking architecture for the receipt upload processing pipeline, including how components connect via DynamoDB, SQS, EFS, S3, and the Mac OCR script.

## Data Flow

```
1. User uploads image
   ↓
2. API Gateway (public internet)
   ↓
3. Upload Receipt Lambda (writes to S3 + DynamoDB)
   ↓
4. Image in S3 (Raw Bucket)
   ↓
5. DynamoDB (OCR Job records)
   ↓
6. SQS Queue (ocr-queue)
   ↓
7. Mac OCR Script (polls SQS, processes OCR)
   ↓
8. SQS Queue (ocr-results-queue)
   ↓
9. Process OCR Results Lambda (reads ocr-results-queue)
   ↓
10. ChromaDB (for merchant resolution - S3 or EFS)
   ↓
11. S3 (delta uploads)
   ↓
12. DynamoDB (compaction trigger)
```

## Component Network Configuration

### 1. API Gateway ✅ **WORKING**

**Location**: Public internet (AWS managed)

**Purpose**: Receives HTTP POST requests from users with images

**Networking**:
- Exposed on public internet (custom domain: dev-api.tylernorlund.com)
- HTTPS (TLS 1.2)
- Regional endpoint (no VPC)
- Invokes Lambda functions

**Configuration**:
```python
# infra/upload_images/infra.py
api = aws.apigatewayv2.Api(...)
# Custom domain with ACM certificate
# Regional endpoint (accessible from internet)
```

---

### 2. Upload Receipt Lambda (submit_job.py) ✅ **WORKING**

**Purpose**: Receives image from API Gateway, uploads to S3, creates DynamoDB records

**VPC Configuration**: **PUBLIC SUBNETS** (no VPC endpoints needed)

**Subnets**:
- `subnet-050ef72e29f7e86bc` (us-east-1a)
- `subnet-0f09bc7acf0894542` (us-east-1b)

**Network Access**:
- ✅ **Internet**: Direct access via Internet Gateway
- ✅ **S3**: Via public internet
- ✅ **DynamoDB**: Via public internet
- ✅ **SQS**: Via public internet

**IAM Permissions**:
- `s3:PutObject`, `s3:GetObject` on raw bucket
- `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:GetItem`
- `sqs:SendMessage` on ocr-queue

**Configuration**:
```python
# infra/upload_images/infra.py
submit_job_lambda = aws.lambda_.Function(
    vpc_config=None,  # Public subnets, direct internet access
    environment={
        "OCR_JOB_QUEUE_URL": ocr_queue.url,
        # ... other env vars
    }
)
```

---

### 3. Raw S3 Bucket ✅ **WORKING**

**Location**: AWS S3 (public internet accessible)

**Purpose**: Stores uploaded raw images

**Access**:
- Upload Receipt Lambda → Writes images
- Process OCR Lambda → Reads images (via VPC endpoint)

**Configuration**:
```python
raw_bucket = aws.s3.Bucket("upload-images-image-bucket")
```

**Networking**: S3 service endpoint (public internet + VPC Gateway Endpoint)

---

### 4. DynamoDB Table ✅ **WORKING**

**Purpose**: Stores OCR jobs, receipts, and processing metadata

**Access**:
- Upload Receipt Lambda → Writes job records
- Process OCR Lambda → Reads/writes job records, receipts
- Mac Script → Reads job records
- Stream Processor → Reads stream events

**Networking**: Gateway endpoint for VPC access (no internet required)

**Configuration**:
```python
# infra/__main__.py
dynamodb_gateway_endpoint = aws.ec2.VpcEndpoint(
    "dynamodb-gateway-dev",
    vpc_id=vpc_id,
    service_name="com.amazonaws.us-east-1.dynamodb",
    vpc_endpoint_type="Gateway",
    route_table_ids=[public_rt.id, private_rt.id],
)
```

**Routes**:
- Private subnets → DynamoDB via Gateway Endpoint ✅
- Public subnets → DynamoDB via Gateway Endpoint ✅

---

### 5. SQS Queues ✅ **WORKING**

#### Queue 1: `ocr-queue` (Job Queue)

**Purpose**: Queue for OCR job requests from Upload Lambda

**Access**:
- **Producer**: Upload Receipt Lambda (from public subnets)
- **Consumer**: Mac OCR Script (from your laptop)

**Message Format**:
```json
{
  "image_id": "uuid",
  "job_id": "uuid",
  "s3_bucket": "bucket-name",
  "s3_key": "path/to/image"
}
```

**Networking**: 
- Upload Lambda sends via public internet
- Mac script receives via public internet
- **No VPC endpoint needed** (both access from internet)

#### Queue 2: `ocr-results-queue` (Results Queue)

**Purpose**: Queue for OCR results from Mac Script

**Access**:
- **Producer**: Mac OCR Script (from your laptop)
- **Consumer**: Process OCR Results Lambda (from private subnets)

**Message Format**:
```json
{
  "job_id": "uuid",
  "image_id": "uuid"
}
```

**Networking**:
- Mac script sends via public internet
- Lambda receives via **SQS VPC Interface Endpoint**

**VPC Endpoint Configuration**:
```python
# infra/__main__.py
sqs_interface_endpoint = aws.ec2.VpcEndpoint(
    "sqs-interface-dev",
    vpc_id=vpc_id,
    service_name="com.amazonaws.us-east-1.sqs",
    vpc_endpoint_type="Interface",
    subnet_ids=[...],  # Private subnet where Lambda runs
    security_group_ids=[vpce_sg.id],
    private_dns_enabled=True,  # Allows sqs.us-east-1.amazonaws.com to resolve privately
)
```

**Security Group Rules**:
```
SG: sg-0d1a96dbf95ed7af6 (VPC Endpoint)
├─ Ingress: TCP/443 from 0.0.0.0/0 (allows Lambda to connect)
└─ Subnets: subnet-02f1818a1e813b126 (where Lambda runs)
```

**Event Source Mapping**:
```python
# infra/upload_images/infra.py
aws.lambda_.EventSourceMapping(
    "ocr-results-mapping",
    event_source_arn=ocr_results_queue.arn,  # Reads from ocr-results-queue
    function_name=process_ocr_lambda.name,     # Triggers Lambda
    batch_size=10,
    enabled=True,
)
```

**How Event Source Mapping Works**:
1. Event source mapping polls SQS from AWS infrastructure (NOT from Lambda's VPC)
2. When messages arrive, it invokes Lambda
3. Lambda only needs to **send responses** to SQS (delete message acknowledgment)
4. Lambda uses SQS VPC endpoint for responses

---

### 6. Mac OCR Script ✅ **WORKING**

**Location**: Your local laptop (public internet)

**Purpose**: Polls `ocr-queue`, runs OCR using Apple Vision Framework, sends results to `ocr-results-queue`

**Network Access**:
- ✅ **SQS**: Direct internet access (no VPC endpoint needed)
- ✅ **S3**: Direct internet access (reads images for OCR)
- ✅ **DynamoDB**: Direct internet access (reads job metadata)

**Configuration**:
```bash
./receipt-ocr \
  --ocr-job-queue-url "https://sqs.us-east-1.amazonaws.com/.../ocr-queue" \
  --ocr-results-queue-url "https://sqs.us-east-1.amazonaws.com/.../ocr-results-queue" \
  --dynamo-table-name "ReceiptsTable-dc5be22" \
  --region "us-east-1" \
  --continuous
```

**Flow**:
1. Polls `ocr-queue` for jobs (receive message)
2. Downloads image from S3
3. Runs OCR using Apple Vision Framework
4. Uploads OCR JSON to S3 artifacts bucket
5. Creates DynamoDB routing decision
6. Sends message to `ocr-results-queue` with `job_id` and `image_id`
7. Processes next job

**Networking**: All traffic via public internet (your home network → AWS)

---

### 7. Process OCR Results Lambda ✅ **WORKING**

**Purpose**: Processes OCR results from Mac Script, creates embeddings, triggers compaction

**VPC Configuration**: **PRIVATE SUBNETS** (requires VPC endpoints)

**Subnets**:
- `subnet-02f1818a1e813b126` (us-east-1f, 10.0.101.0/24)
- `subnet-0bb59954a84999494` (us-east-1f, 10.0.102.0/24)

**Security Group**: `sg-025b2a030a20e6037`

**Network Access**:

#### A. SQS Access ✅ **VPC ENDPOINT**

**How it works**:
- Event source mapping polls SQS from AWS infrastructure (outside VPC)
- Lambda receives events via AWS Lambda service
- Lambda sends responses (delete message) via **SQS VPC Interface Endpoint**
- Endpoint provides private DNS resolution for `sqs.us-east-1.amazonaws.com`

**Configuration**:
```python
# infra/__main__.py
sqs_interface_endpoint = aws.ec2.VpcEndpoint(
    "sqs-interface-dev",
    vpc_id=vpc_id,
    service_name="com.amazonaws.us-east-1.sqs",
    vpc_endpoint_type="Interface",
    subnet_ids=[
        "subnet-0f09bc7acf0894542",  # us-east-1b (public)
        "subnet-02f1818a1e813b126",  # us-east-1f (private) ✅ Lambda subnet
        "subnet-050ef72e29f7e86bc",  # us-east-1a (public)
    ],
    security_group_ids=["sg-0d1a96dbf95ed7af6"],
    private_dns_enabled=True,
)
```

**Security Group Rules**:
```
VPC Endpoint SG (sg-0d1a96dbf95ed7af6):
├─ Ingress: TCP/443 from 0.0.0.0/0
└─ Allows: Lambda in private subnet to send SQS messages

Lambda SG (sg-025b2a030a20e6037):
└─ Egress: TCP/443 to SQS (via VPC endpoint)
```

**Route**:
```
Lambda (private subnet)
├─ Sends HTTP request to sqs.us-east-1.amazonaws.com
├─ DNS resolves to VPC endpoint (10.0.0.60)
├─ Traffic routed via VPC endpoint to SQS service
└─ No internet required ✅
```

#### B. S3 Access ✅ **GATEWAY ENDPOINT**

**How it works**:
- Lambda accesses S3 via **S3 Gateway Endpoint** (no internet)
- All S3 traffic from VPC routes to S3 service via private route
- Faster and cheaper than internet

**Configuration**:
```python
# infra/__main__.py
s3_gateway_endpoint = aws.ec2.VpcEndpoint(
    "s3-gateway-dev",
    vpc_id=vpc_id,
    service_name="com.amazonaws.us-east-1.s3",
    vpc_endpoint_type="Gateway",
    route_table_ids=[public_rt.id, private_rt.id],
)
```

**Route**:
```
Lambda (private subnet)
├─ Sends S3 request (PUT, GET)
├─ Route table routes to S3 Gateway Endpoint
├─ Traffic goes via private AWS network
└─ No internet required ✅
```

#### C. DynamoDB Access ✅ **GATEWAY ENDPOINT**

**How it works**:
- Lambda accesses DynamoDB via **DynamoDB Gateway Endpoint**
- All DynamoDB traffic from VPC routes to DynamoDB service
- No internet required

**Configuration**:
```python
# infra/__main__.py
dynamodb_gateway_endpoint = aws.ec2.VpcEndpoint(
    "dynamodb-gateway-dev",
    vpc_id=vpc_id,
    service_name="com.amazonaws.us-east-1.dynamodb",
    vpc_endpoint_type="Gateway",
    route_table_ids=[public_rt.id, private_rt.id],
)
```

**Route**:
```
Lambda (private subnet)
├─ Sends DynamoDB request
├─ Route table routes to DynamoDB Gateway Endpoint
├─ Traffic goes via private AWS network
└─ No internet required ✅
```

#### D. EFS Access ✅ **MOUNT TARGET**

**How it works**:
- EFS is mounted at `/mnt/chroma` in Lambda
- Lambda accesses EFS via **EFS Mount Target** in same subnet
- Uses NFS protocol over private network

**Configuration**:
```python
# infra/chromadb_compaction/components/efs.py
mount_targets = aws.efs.MountTarget(
    f"{name}-mt-0",
    file_system_id=self.file_system.id,
    subnet_id=subnet_ids[0],  # subnet-02f1818a1e813b126 (Lambda subnet)
    security_group_ids=[efs_sg.id],
)
```

**Security Group Rules**:
```
EFS SG (sg-0ee1812a0df4a9730):
├─ Ingress: TCP/2049 (NFS) from Lambda SG (sg-025b2a030a20e6037)
└─ Allows: Lambda to access EFS

Lambda SG (sg-025b2a030a20e6037):
└─ Egress: TCP/2049 to EFS mount target
```

**Mount Configuration**:
```python
# infra/upload_images/infra.py
process_ocr_lambda_config = {
    "file_system_config": {
        "arn": "arn:aws:efs:...:access-point/fsap-...",
        "local_mount_path": "/mnt/chroma",
    }
}
```

**Current Status**: EFS is mounted but disabled due to timeouts:
```python
"CHROMADB_STORAGE_MODE": "s3",  # EFS too slow, using S3 instead
```

**Why EFS Times Out**:
1. NFS protocol over network is slow
2. ChromaDB creates many small files
3. `shutil.copytree` hangs on large directories (3GB snapshot)
4. Lambda timeout (10 min) not enough for EFS operations

**Route**:
```
Lambda (private subnet)
├─ NFS operations to /mnt/chroma
├─ Traffic goes to EFS mount target (10.0.101.0/24)
├─ Mount target forwards to EFS service
└─ Private network, no internet ✅
```

#### E. Internet Access ✅ **NAT INSTANCE**

**Why needed**: OpenAI API, Google Places API for merchant resolution and embeddings

**How it works**:
- Lambda is in private subnet
- Route table routes `0.0.0.0/0` → NAT instance
- NAT instance has public IP and forwards to internet

**Configuration**:
```python
# infra/nat.py
nat = NATInstance(
    "nat-dev",
    vpc_id=vpc_id,
    public_subnet_ids=public_subnet_ids,
    private_subnet_ids=private_subnet_ids,
    # Creates NAT instance in public subnet
    # Creates route in private subnet route table
)
```

**NAT Instance**:
```
Instance: i-0fd755fe8e869a002
├─ Type: t3.micro
├─ State: running
├─ Private IP: 10.0.0.240
├─ Public IP: 44.219.55.167
└─ Route: Internet Gateway
```

**Route**:
```
Lambda (private subnet)
├─ Sends HTTPS request to api.openai.com
├─ Route table routes to NAT instance
├─ NAT instance forwards via Internet Gateway
└─ Gets response via NAT
```

#### F. CloudWatch Logs Access ✅ **INTERFACE ENDPOINT**

**How it works**:
- Lambda sends logs to CloudWatch Logs via **Logs Interface Endpoint**
- No internet required for logging

**Configuration**:
```python
# infra/__main__.py
logs_interface_endpoint = aws.ec2.VpcEndpoint(
    "logs-interface-dev",
    vpc_id=vpc_id,
    service_name="com.amazonaws.us-east-1.logs",
    vpc_endpoint_type="Interface",
    subnet_ids=public_subnet_ids,  # Could also be private
    security_group_ids=[vpce_sg.id],
    private_dns_enabled=True,
)
```

**Route**:
```
Lambda (private subnet)
├─ Sends logs to logs.us-east-1.amazonaws.com
├─ DNS resolves to VPC Interface Endpoint
├─ Traffic goes via private network
└─ No internet required ✅
```

---

### 8. ChromaDB Storage ✅ **S3 (EFS DISABLED)**

**Purpose**: Vector database for merchant resolution

**Storage Modes**:

#### Mode 1: S3 (CURRENT) ✅

**How it works**:
- ChromaDB snapshots stored in S3
- Lambda downloads snapshot from S3 to `/tmp`
- Lambda creates embeddings using local ChromaDB
- Uploads deltas to S3

**Configuration**:
```python
"CHROMADB_STORAGE_MODE": "s3"
```

**Networking**:
- Download: S3 Gateway Endpoint (private network)
- Upload: S3 Gateway Endpoint (private network)

**Performance**: ~60-90 seconds for download, ~30-60 seconds for upload

#### Mode 2: EFS (DISABLED) ⚠️

**How it works**:
- ChromaDB snapshots stored on EFS
- Lambda would copy from EFS to `/tmp`
- Lambda creates embeddings
- Uploads deltas to S3
- Would copy updated snapshot back to EFS

**Configuration**:
```python
"CHROMADB_STORAGE_MODE": "efs"  # or "auto"
```

**Networking**:
- Read: EFS mount target (private network)
- Write: EFS mount target (private network)
- Still needs S3 for delta uploads

**Performance**: ~10-15 seconds for EFS copy, but hangs/times out (not reliable)

**Why Disabled**: Timeouts during `shutil.copytree` operations over NFS

---

### 9. Stream Processor Lambda ✅ **WORKING**

**Purpose**: Processes DynamoDB stream events, triggers compaction via SQS

**VPC Configuration**: Public subnets (no VPC needed for DynamoDB stream)

**Network Access**:
- ✅ **DynamoDB**: Via Gateway Endpoint
- ✅ **CloudWatch**: Via Interface Endpoint
- ✅ **SQS**: Via Interface Endpoint

---

### 10. Compaction Lambda ✅ **WORKING**

**Purpose**: Compacts ChromaDB deltas into snapshots

**VPC Configuration**: Private subnets (same as Process OCR Lambda)

**Network Access**: 
- Same as Process OCR Lambda
- Uses **EFS** successfully (Elastic throughput mode)

**Configuration**:
```python
"CHROMADB_STORAGE_MODE": "auto"  # Uses EFS if available
"timeout": 300,  # 5 minutes
```

**Why it works with EFS**:
- Fewer small file operations
- One-time copy per compaction (not per request)
- Elastic throughput provides fast transfers

---

## Network Summary

### For Process OCR Results Lambda

**Subnets**: Private (us-east-1f)

**VPC Endpoints Required**:
1. ✅ **SQS Interface Endpoint** - For sending SQS delete messages
2. ✅ **S3 Gateway Endpoint** - For downloading/uploading snapshots and images
3. ✅ **DynamoDB Gateway Endpoint** - For reading/writing job metadata
4. ✅ **CloudWatch Logs Interface Endpoint** - For sending logs
5. ✅ **EFS Mount Target** - For ChromaDB storage (but disabled due to timeouts)

**Internet Access Required**:
- ✅ **NAT Instance** - For OpenAI API (merchant resolution embeddings)
- ✅ **NAT Instance** - For Google Places API (merchant validation)

**Security Groups**:
- Lambda SG (`sg-025b2a030a20e6037`): Outbound to all services
- VPC Endpoint SG (`sg-0d1a96dbf95ed7af6`): Ingress TCP/443 from Lambda SG
- EFS SG (`sg-0ee1812a0df4a9730`): Ingress TCP/2049 (NFS) from Lambda SG

**Current Status**: ✅ **WORKING** (S3-only mode for ChromaDB)

---

## Troubleshooting

### Issue: "Messages stuck in queue"

**Cause**: Lambda times out, messages remain in-flight

**Solution**: Increase Lambda timeout or fix timeout issue

### Issue: "EFS timeouts"

**Cause**: NFS operations too slow for Lambda constraints

**Solution**: Use S3 mode instead of EFS

### Issue: "Lambda can't connect to SQS"

**Check**:
1. VPC endpoint exists in same subnet as Lambda
2. Security group allows TCP/443 from Lambda SG
3. Private DNS enabled on VPC endpoint

### Issue: "Lambda can't access internet"

**Check**:
1. NAT instance running
2. Route table has 0.0.0.0/0 → NAT instance
3. NAT instance security group allows outbound traffic
4. Internet Gateway attached to VPC

---

## Cost Analysis

**NAT Instance**: ~$6/month (t3.micro reserved)

**VPC Endpoints**:
- S3 Gateway: Free
- DynamoDB Gateway: Free
- SQS Interface: ~$7/month (per AZ)
- CloudWatch Logs Interface: ~$7/month (per AZ)
- **Total**: ~$14/month

**EFS**: 
- Storage: $0.30/GB/month (costs depend on usage)
- Throughput (Elastic): $0.05/MiB transferred

**vs NAT Gateway Alternative**:
- NAT Gateway: ~$32/month + data transfer costs
- **Savings**: ~$18/month with NAT instance

---

## Recommendations

✅ **Current configuration is optimal**:
- Upload Lambda: S3 mode (fast, reliable)
- Compaction Lambda: EFS mode (performance benefit)
- All networking correctly configured
- Cost-efficient (NAT instance vs NAT Gateway)

**No changes needed** - Upload images and test!

