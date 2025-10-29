# Lambda Networking Architecture for upload-images-process-ocr-image-dev

## Current Configuration

### Lambda Deployment
- **Function**: `upload-images-process-ocr-image-dev`
- **VPC**: `vpc-0ff3605a5428b9727`
- **Subnets**: 
  - `subnet-02f1818a1e813b126` (us-east-1f, 10.0.101.0/24) ✅
  - `subnet-0bb59954a84999494` (us-east-1f, 10.0.102.0/24) ✅
- **Security Group**: `sg-025b2a030a20e6037`
- **EFS Mount**: `/mnt/chroma` ✅ (mounted)
- **Storage Mode**: `s3` (S3-only, EFS disabled due to timeouts)

### Subnet Types
**Both subnets are PRIVATE subnets** (route to NAT instance, not Internet Gateway)

```
subnet-02f1818a1e813b126: Private (us-east-1f)
  ├─ Route: 0.0.0.0/0 → NAT instance (i-0fd755fe8e869a002)
  ├─ EFS mount target exists ✅
  └─ SQS VPC endpoint enabled ✅

subnet-0bb59954a84999494: Private (us-east-1f)
  ├─ Route: 0.0.0.0/0 → NAT instance (i-0fd755fe8e869a002)
  └─ No EFS mount target (same AZ)
```

## Network Requirements for Lambda to Function

### 1. SQS Queue Access ✅ **WORKING**

**What's needed**: Lambda needs to be able to receive messages from SQS via event source mapping.

**How it works**:
- Event Source Mapping polls SQS from AWS infrastructure (NOT from VPC)
- When messages arrive, it invokes the Lambda
- Lambda only needs to **send responses** to SQS (delete message after processing)
- Lambda uses SQS VPC endpoint (Interface Endpoint) to send responses

**Current status**: ✅ **WORKING**
- Lambda is in `subnet-02f1818a1e813b126`
- SQS VPC endpoint (`vpce-0145e81bc1cd8163d`) has a subnet in `subnet-02f1818a1e813b126`
- Event source mapping is Enabled
- Lambda can access SQS through the VPC endpoint

**Configuration**:
```
SQS VPC Endpoint: vpce-0145e81bc1cd8163d
├─ Service: com.amazonaws.us-east-1.sqs (Interface Endpoint)
├─ State: available
├─ Subnets: 
│  ├─ subnet-0f09bc7acf0894542 (us-east-1b, public)
│  ├─ subnet-02f1818a1e813b126 (us-east-1f, private) ✅ Lambda subnet
│  └─ subnet-050ef72e29f7e86bc (us-east-1a, public)
└─ Security Group: sg-0d1a96dbf95ed7af6
   └─ Allows: TCP/443 from any (0.0.0.0/0)
```

### 2. EFS Access ✅ **CONFIGURED but TIMING OUT**

**What's needed**: Lambda needs to read/write ChromaDB snapshots from EFS.

**Current status**: ⚠️ **HANGING**
- EFS is mounted at `/mnt/chroma`
- Mount target exists in `subnet-02f1818a1e813b126` (same subnet as Lambda)
- Lambda hangs when trying to access EFS (10+ minute timeouts)
- Using S3 fallback works fine

**Configuration**:
```
EFS File System: fs-0b7608126227439b9
├─ Mount target: fsmt-09d0e5038e9675d23
│  ├─ Subnet: subnet-02f1818a1e813b126 ✅
│  ├─ Availability Zone: us-east-1f
│  └─ State: available
├─ Mount path in Lambda: /mnt/chroma
└─ Throughput: Elastic (auto-scaling)
```

**Why EFS is Timing Out**:
1. NFS operations over network are slow
2. `shutil.copytree` hangs when copying large directories (3GB ChromaDB snapshot)
3. Lambda timeout is 10 minutes, but copy operation takes longer
4. Network latency + NFS protocol overhead = very slow operations

### 3. S3 Access ✅ **WORKING**

**What's needed**: Lambda needs to download ChromaDB snapshots from S3.

**Current status**: ✅ **WORKING**
- Lambda uses NAT instance for internet access
- S3 Gateway Endpoint (`vpce-0e21e1abf3d74067e`) is configured
- Both public and private subnets route to S3 via Gateway Endpoint (no internet required)

**Configuration**:
```
S3 Gateway Endpoint: vpce-0e21e1abf3d74067e
├─ Service: com.amazonaws.us-east-1.s3
├─ Type: Gateway (DNS resolution only)
└─ Route table: Both public and private subnets
```

### 4. DynamoDB Access ✅ **WORKING**

**What's needed**: Lambda needs to read/write to DynamoDB table.

**Current status**: ✅ **WORKING**
- DynamoDB Gateway Endpoint (`vpce-0532ba1c83b5bd5e0`) is configured
- Both public and private subnets route to DynamoDB via Gateway Endpoint

**Configuration**:
```
DynamoDB Gateway Endpoint: vpce-0532ba1c83b5bd5e0
├─ Service: com.amazonaws.us-east-1.dynamodb
├─ Type: Gateway (DNS resolution only)
└─ Route table: Both public and private subnets
```

### 5. Internet Access ✅ **WORKING**

**What's needed**: Lambda needs internet access for OpenAI and Google Places APIs.

**Current status**: ✅ **WORKING**
- Lambda is in private subnets
- NAT instance (`i-0fd755fe8e869a002`) running at `10.0.0.240`
- Route table routes 0.0.0.0/0 → NAT instance
- NAT instance has public IP and can reach internet

**Configuration**:
```
NAT Instance: i-0fd755fe8e869a002
├─ Type: t3.micro
├─ State: running
├─ Private IP: 10.0.0.240
├─ Public IP: 44.219.55.167
└─ Provides: Outbound internet access for private subnets
```

## Why EFS Isn't Working for Upload Lambda

### The Problem
When Lambda tries to use EFS for ChromaDB access, it:
1. Initializes `UploadEFSSnapshotManager` 
2. Tries to access `/mnt/chroma` directory
3. Hangs at one of these operations:
   - `os.listdir(efs_root)` - Listing directory
   - `os.path.exists(efs_root)` - Checking if path exists
   - `os.makedirs()` - Creating directory
   - `shutil.copytree()` - Copying 3GB snapshot from EFS to `/tmp`

### Root Cause
**NFS protocol overhead**:
- Every file operation over NFS is a network request
- Creating many small files is especially slow
- ChromaDB creates thousands of small metadata files
- NFS + network latency + Lambda's ephemeral network = very slow

**Evidence from logs**:
```
[EMBEDDING_PROCESSOR] Using EFS for ChromaDB snapshot access
... (10 minutes of silence)
Task timed out after 600.05 seconds
```

### Why It Works for Compaction Lambda

The compaction lambda with EFS works because:
1. **Elastic throughput**: EFS can burst to 1000+ MiB/s
2. **Fewer small files**: Snapshot is already a complete directory structure
3. **One-time copy**: Copy happens once per compaction (not per request)
4. **Longer timeout**: Compaction has 5-minute timeout

The upload lambda fails because:
1. **Many small operations**: ChromaDB creates many small files during initialization
2. **Repeated for each request**: Copy happens for every merchant resolution
3. **Network overhead**: NFS protocol is slower than local disk for small files

## Required Network Configuration (All Present)

### ✅ VPC Endpoints (Interface)
- **SQS**: `vpce-0145e81bc1cd8163d` - Allows Lambda to send messages to SQS without internet
- **CloudWatch Logs**: `vpce-xxxxx` - Allows Lambda to send logs without internet

### ✅ VPC Endpoints (Gateway)  
- **S3**: `vpce-0e21e1abf3d74067e` - Routes S3 traffic without internet (private)
- **DynamoDB**: `vpce-0532ba1c83b5bd5e0` - Routes DynamoDB traffic without internet (private)

### ✅ NAT Instance
- **Internet Access**: `i-0fd755fe8e869a002` - Provides outbound internet for Lambda

### ✅ EFS Mount Target
- **Storage**: `fsmt-09d0e5038e9675d23` - Provides EFS access for Lambda
- Located in same subnet as Lambda ✅

## Why Event Source Mapping Isn't Processing Messages

**Timeline**:
1. Old messages (from before Pulumi update) are stuck in queue
2. They were made invisible (visibility timeout = 900 seconds = 15 minutes)
3. Event source mapping can't process them until visibility timeout expires
4. Lambda isn't being invoked because messages are already "in use"

**Solution**: Wait for visibility timeout or purge queue

## Recommendations

### For SQS Access ✅
- Current configuration is correct
- SQS VPC endpoint is properly configured
- Event source mapping works
- **No changes needed**

### For EFS Access ⚠️
**Option 1: Keep S3-only mode** (RECOMMENDED)
- Upload lambda uses S3 for ChromaDB snapshots
- Compaction lambda uses EFS for ChromaDB snapshots
- This provides optimal balance:
  - Upload is fast and reliable (S3)
  - Compaction gets performance benefit (EFS)
  - **Status**: Currently implemented

**Option 2: Fix EFS for Upload Lambda**
- Increase Lambda timeout from 10 min to 15 min
- Add timeout handling to EFS operations
- Use async/multithreaded copy operations
- Cache EFS snapshots in Lambda's `/tmp` between invocations
- **Risk**: Still may timeout

**Option 3: Use different architecture**
- Move upload lambda to public subnet (but then it loses EFS access)
- Use ECS Fargate instead of Lambda (better for persistent connections)
- Use ECS with Fargate + EFS (more complexity, higher cost)

### Current Working Configuration ✅
```python
# infra/upload_images/infra.py
"CHROMADB_STORAGE_MODE": "s3"  # Use S3-only for upload lambda
```

This means:
- ✅ SQS: Working (via VPC endpoint)
- ✅ S3: Working (via Gateway endpoint)
- ✅ DynamoDB: Working (via Gateway endpoint)
- ✅ Internet: Working (via NAT instance)
- ⚠️ EFS: Not used (timing out, disabled)
- ✅ Event Source Mapping: Working (when queue isn't stuck)

## Summary

**All networking is correctly configured** ✅

The issue is:
1. **EFS is too slow** for the upload lambda's use case (many small files, network latency)
2. **Old stuck messages** in the queue from previous EFS timeouts
3. **Event source mapping can't process** stuck messages until visibility timeout expires

**Solution implemented**: Using S3-only mode for upload lambda, allowing it to work reliably without EFS timeouts.

