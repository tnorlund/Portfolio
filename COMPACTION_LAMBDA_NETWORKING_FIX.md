# Compaction Lambda EFS Networking Fix

## Problem

The `chromadb-dev-docker-dev` Lambda cannot connect to EFS:
```
EFSMountConnectivityException: The function couldn't connect to the Amazon EFS file system with access point arn:aws:elasticfilesystem:us-east-1:681647709217:access-point/fsap-06905e1f62ead9feb. Check your network configuration and try again.
```

## Root Cause

1. **Lambda Subnets**: Lambda is configured with 3 subnets (2 public, 1 private)
2. **EFS Mount Target**: Only exists in the private subnet (`subnet-02f1818a1e813b126`)
3. **Conflict**: When Lambda is in VPC with multiple subnets, it creates ENIs. ENIs in the 2 public subnets cannot reach EFS because there's no mount target in those subnets.

## Solution

**Updated `infra/__main__.py`** to place the compaction Lambda **only in private subnets**:

```python
# Compaction lambda needs to be in private subnets for EFS access
# (EFS mount target is in subnet-02f1818a1e813b126, which is the first private subnet)
compaction_lambda_subnets = nat.private_subnet_ids  # Both private subnets for Lambda

chromadb_infrastructure = create_chromadb_compaction_infrastructure(
    name=f"chromadb-{pulumi.get_stack()}",
    dynamodb_table_arn=dynamodb_table.arn,
    dynamodb_stream_arn=dynamodb_table.stream_arn,
    chromadb_buckets=shared_chromadb_buckets,
    base_images=base_images,
    vpc_id=public_vpc.vpc_id,
    subnet_ids=compaction_lambda_subnets,  # Private subnets only for Lambda
    lambda_security_group_id=security.sg_lambda_id,
)
```

This ensures:
- Lambda ENIs are only created in private subnets
- All Lambda ENIs can reach the EFS mount target
- SQS access via VPC endpoint (already configured for private subnets)

## Deployment Issue

Pulumi is trying to create EFS mount targets that already exist, causing conflicts:
```
MountTargetConflict: mount target already exists in this AZ
```

**To resolve**: These conflicts will need to be handled (import existing resources or manually clean up conflicting mount targets).

## Expected Result After Fix

1. Lambda creates ENIs only in private subnets (`subnet-02f1818a1e813b126`, `subnet-0bb59954a84999494`)
2. Lambda can reach EFS mount target via ENI in `subnet-02f1818a1e813b126`
3. Lambda can reach SQS via VPC endpoint
4. Lambda can reach internet for API calls via NAT instance

