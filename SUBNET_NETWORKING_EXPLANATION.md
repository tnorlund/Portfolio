# Public vs Private Subnets - Networking Explained

## Key Differences

### Public Subnets
```
Internet Gateway (igw-xxxx)
    ↑
Route: 0.0.0.0/0 → Internet Gateway
    ↑
Public Subnet (10.0.0.0/24, 10.0.1.0/24)
    ↓
Resources get PUBLIC IPs
    ↓
Direct internet access ✅
```

**Characteristics**:
- Route table includes: `0.0.0.0/0 → igw-xxxx` (Internet Gateway)
- Instances/Lambdas can have public IP addresses
- Direct outbound internet access (no NAT required)
- Can receive inbound connections from internet (if security groups allow)
- Less secure (directly accessible from internet)

**Use cases**: 
- Load balancers (need public access)
- EC2 instances that need direct internet access
- Services that don't require added network isolation

### Private Subnets
```
Private Subnet (10.0.101.0/24, 10.0.102.0/24)
    ↓
Resources get PRIVATE IPs only
    ↓
Route: 0.0.0.0/0 → NAT Instance (i-0fd755...)
    ↓
NAT Instance (in public subnet)
    ↓
Internet Gateway
    ↓
Internet
```

**Characteristics**:
- Route table includes: `0.0.0.0/0 → i-xxxxx` (NAT Instance/Gateway)
- Instances/Lambdas get ONLY private IP addresses
- Outbound internet access via NAT (not direct)
- Can NOT receive inbound connections from internet (more secure)
- More secure (isolated from direct internet access)

**Use cases**:
- Databases (no internet access needed)
- Application servers (outbound only)
- Lambdas that need outbound internet but shouldn't be directly accessible

---

## For Each Service in Your Stack

### 1. Upload Receipt Lambda (submit_job.py) - PUBLIC SUBNET ✅

**Why Public?** 
- Needs to receive HTTPS requests from API Gateway
- Needs outbound internet access for S3, DynamoDB, SQS
- No VPC endpoints needed (uses public internet)

**Route Table**:
```
10.0.0.0/16 → local (VPC internal)
0.0.0.0/0   → igw-09df0f221c0788424 (Internet Gateway) ✅
```

**Internet Access**: Direct via Internet Gateway

**Firewall**: Security groups control what traffic is allowed

---

### 2. ChromaDB ECS Service - PUBLIC SUBNET ✅

**Why Public?**
- Needs to serve HTTP requests from other services
- Needs to be discoverable via Service Discovery
- Uses ECS service networking

**Route Table**:
```
10.0.0.0/16 → local (VPC internal)
0.0.0.0/0   → igw-09df0f221c0788424 (Internet Gateway) ✅
```

**Internet Access**: Direct via Internet Gateway

---

### 3. Process OCR Lambda (container OCR) - PRIVATE SUBNET ✅

**Why Private?**
- Should not be directly accessible from internet
- Only needs outbound internet access (for APIs like OpenAI)
- Needs VPC endpoints for internal AWS services (S3, DynamoDB, SQS)

**Route Table**:
```
10.0.0.0/16 → local (VPC internal)
0.0.0.0/0   → i-0fd755fe8e869a002 (NAT Instance) ✅
             (for OpenAI, Google Places API)
```

**Internet Access**: Via NAT Instance in public subnet

**Outbound Connections**:
- **OpenAI API**: Lambda → NAT → Internet Gateway → Internet ✅
- **Google Places API**: Lambda → NAT → Internet Gateway → Internet ✅

**AWS Service Access**:
- **S3**: Via Gateway Endpoint (private AWS network) ✅
- **DynamoDB**: Via Gateway Endpoint (private AWS network) ✅
- **SQS**: Via Interface Endpoint (private AWS network) ✅
- **CloudWatch Logs**: Via Interface Endpoint (private AWS network) ✅
- **EFS**: Via mount target in same subnet (private network) ✅

**Why VPC Endpoints for AWS Services?**
- Lambda is in **private subnet** (no internet)
- But still needs to access S3, DynamoDB, etc.
- **Solution**: VPC Endpoints provide private connectivity to AWS services
  - No internet required ✅
  - Faster (private AWS network)
  - Cheaper (no NAT data transfer costs)
  - More secure (stays within AWS network)

---

### 4. Compaction Lambda - PRIVATE SUBNET ✅

**Same as Process OCR Lambda**:
- Private subnet for security
- NAT for outbound internet
- VPC endpoints for AWS services

---

## Why This Architecture?

### Security ✅
```
Internet
  ↓
API Gateway (public endpoint)
  ↓
Upload Receipt Lambda (public subnet)
  ↓
Image uploaded to S3
  ↓
DynamoDB record created
  ↓
Message to SQS queue
  ↓
Mac Script processes
  ↓
Message to SQS results queue
  ↓
Process OCR Lambda (PRIVATE subnet - isolated) ✅
  ↓
Creates embeddings
  ↓
Uploads deltas to S3
```

**Note**: Process OCR Lambda is in **private subnet** and cannot be directly accessed from internet. Only invoked by AWS Lambda service (event source mapping).

### Cost Optimization ✅

**VPC Endpoints (Free or cheap)**:
- S3 Gateway Endpoint: FREE
- DynamoDB Gateway Endpoint: FREE
- SQS Interface Endpoint: ~$7/month per AZ
- CloudWatch Logs Interface Endpoint: ~$7/month per AZ

**Without endpoints** (if using NAT for everything):
- NAT Gateway: ~$32/month + data transfer costs
- Data transfer for S3: ~$0.09/GB
- Data transfer for DynamoDB: ~$0.05/GB

**Savings with current setup**: ~$20-30/month

---

## Network Flow Diagrams

### Upload Receipt Lambda (Public Subnet)

```
User (Internet)
  ↓ HTTPS
API Gateway
  ↓ HTTPS  
Upload Receipt Lambda (public subnet)
  ↓ HTTPS (direct internet)
S3 (uploads image)
  ↓ HTTPS (direct internet)
DynamoDB (creates record)
  ↓ HTTPS (direct internet)
SQS (sends message)
```

**Features**:
- Direct internet access ✅
- No NAT needed ✅
- Can receive connections from internet ✅
- Less secure (public subnet)

---

### Process OCR Lambda (Private Subnet)

```
SQS Queue (AWS service)
  ↓ AWS Lambda service invokes
Process OCR Lambda (PRIVATE subnet)
  ↓
  ├─ SQS delete message → SQS Interface Endpoint (private) ✅
  ├─ Download from S3 → S3 Gateway Endpoint (private) ✅
  ├─ Read/write DynamoDB → DynamoDB Gateway Endpoint (private) ✅
  ├─ Send logs → Logs Interface Endpoint (private) ✅
  ├─ Access EFS → EFS mount target (private) ✅
  └─ OpenAI API → NAT Instance → Internet Gateway → Internet ✅
```

**Features**:
- Isolated from internet (private subnet) ✅
- Internet access via NAT (outbound only) ✅
- AWS services via VPC endpoints (no internet) ✅
- More secure (not directly accessible) ✅
- Cost-effective (no NAT data transfer for AWS services)

---

## Event Source Mapping Invocation

**Important**: Event Source Mapping invocation does NOT use Lambda's VPC

```
SQS Queue
  ↓
Event Source Mapping (AWS-managed service)
  ↓ (invokes via AWS Lambda service API)
Lambda Function
  ↓ (executes in VPC)
VPC Endpoints / NAT
```

**Why Lambda in VPC can receive invocations**:
1. AWS Lambda service invokes function (managed service, not from VPC)
2. Lambda execution ENI is attached to VPC
3. Lambda code runs within VPC
4. Lambda can use VPC endpoints for outbound AWS service access

**Event Source Mapping**:
- Polls SQS from AWS infrastructure (outside VPC) ✅
- Invokes Lambda via AWS Lambda service API ✅
- Lambda code runs within VPC (has access to VPC resources) ✅

---

## Summary Table

| Service | Subnet Type | Internet Access | S3 Access | DynamoDB Access | Security |
|---------|-------------|----------------|-----------|-----------------|----------|
| Upload Receipt Lambda | Public | Direct (IGW) | Direct internet | Direct internet | Low (exposed) |
| ChromaDB ECS | Public | Direct (IGW) | Direct internet | Direct internet | Medium (ALB) |
| Process OCR Lambda | Private | Via NAT | VPC Gateway Endpoint | VPC Gateway Endpoint | High (isolated) |
| Compaction Lambda | Private | Via NAT | VPC Gateway Endpoint | VPC Gateway Endpoint | High (isolated) |

---

## Why Process OCR Lambda Uses Private Subnet

**Benefits**:
1. **Security**: Not directly accessible from internet
2. **Isolation**: Network-level isolation from public internet
3. **Cost**: VPC endpoints cheaper than NAT Gateway for AWS services
4. **Performance**: VPC endpoints faster than internet routing

**Trade-offs**:
1. **Complexity**: Need NAT instance + VPC endpoints
2. **Cold starts**: Lambda in VPC has longer cold start time
3. **ENI creation**: Lambda creates ENI in subnet (takes 10-30 seconds)

**For your use case**: Private subnet is correct because:
- Lambda only needs to be invoked by AWS (event source mapping)
- Lambda needs internet for OpenAI/Google Places (NAT provides this)
- Lambda needs AWS services (VPC endpoints provide this)
- Security is important (receipt data processing)

---

## Troubleshooting

### Issue: "Lambda can't reach internet"

**Private subnet check**:
- Is NAT instance running? ✅ (i-0fd755fe8e869a002)
- Does route table have `0.0.0.0/0 → NAT instance`? ✅
- Does NAT instance have public IP? ✅ (44.219.55.167)

### Issue: "Lambda can't reach S3/DynamoDB"

**VPC endpoint check**:
- Does VPC endpoint exist? ✅
- Is it in same subnet as Lambda? For Interface endpoints, yes ✅
- Are security groups correct? ✅

### Issue: "Lambda never gets invoked"

**Event source mapping check**:
- Is mapping enabled? ✅
- Is Lambda function in Active state? ✅
- Are there messages in queue? Check queue attributes

---

## Current Configuration Status

✅ **All services correctly configured**:
- Upload Receipt Lambda: Public subnet (needs direct internet)
- Process OCR Lambda: Private subnet (secure, uses VPC endpoints)
- All VPC endpoints configured
- NAT instance running
- Security groups configured
- Routes configured

**No network changes needed** - the issue is stuck messages in queue, not networking.

