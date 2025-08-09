# ChromaDB On-Demand Fargate Architecture

## Overview

This architecture provides a cost-effective way to use ChromaDB for complex vector queries during Step Function workflows. Instead of running ChromaDB 24/7, we spin up a Fargate task on-demand, use it for parallel Lambda queries, then tear it down.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                     Step Function Workflow                   │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────┐                                           │
│  │   Step 1:    │                                           │
│  │ Start Fargate│──► Fargate Task (ChromaDB Server)        │
│  │ Load Snapshot│     - Pulls snapshot from S3              │
│  └──────────────┘     - Runs on port 8000                  │
│         │              - Private IP in VPC                  │
│         ▼                                                   │
│  ┌──────────────────────────────────────┐                  │
│  │         Step 2: Parallel Tasks        │                  │
│  ├──────────────────────────────────────┤                  │
│  │                                        │                  │
│  │  Lambda A ──► Query ChromaDB ◄──┐     │                  │
│  │  Lambda B ──► Query ChromaDB ◄──┼─────┼──► Fargate      │
│  │  Lambda C ──► Query ChromaDB ◄──┘     │     Task        │
│  │  Lambda D ──► Process Results         │    (Running)     │
│  │                                        │                  │
│  └──────────────────────────────────────┘                  │
│         │                                                   │
│         ▼                                                   │
│  ┌──────────────┐                                           │
│  │   Step 3:    │                                           │
│  │ Stop Fargate │──► Terminates Fargate Task               │
│  │  Save State  │     Optionally saves to S3               │
│  └──────────────┘                                           │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Components

### 1. Initialization Step (Start Fargate)

**Purpose**: Launch ChromaDB server and prepare it with data

**Implementation**:
```python
# Lambda or ECS RunTask
def start_chromadb_fargate(event, context):
    # Start Fargate task
    response = ecs_client.run_task(
        cluster='my-cluster',
        taskDefinition='chromadb-server',
        launchType='FARGATE',
        networkConfiguration={
            'awsvpcConfiguration': {
                'subnets': ['subnet-xxx'],
                'securityGroups': ['sg-chromadb'],
                'assignPublicIp': 'DISABLED'
            }
        },
        overrides={
            'containerOverrides': [{
                'name': 'chromadb',
                'environment': [
                    {'name': 'S3_SNAPSHOT_PATH', 'value': event['snapshot_path']},
                    {'name': 'COLLECTION_NAME', 'value': event['collection_name']}
                ]
            }]
        }
    )
    
    # Wait for task to be running and healthy
    task_arn = response['tasks'][0]['taskArn']
    waiter = ecs_client.get_waiter('tasks_running')
    waiter.wait(cluster='my-cluster', tasks=[task_arn])
    
    # Get private IP
    task = ecs_client.describe_tasks(cluster='my-cluster', tasks=[task_arn])
    private_ip = task['tasks'][0]['attachments'][0]['details'][...]['value']
    
    # Test ChromaDB is ready
    chromadb_client = chromadb.HttpClient(host=private_ip, port=8000)
    chromadb_client.heartbeat()  # Will throw if not ready
    
    return {
        'task_arn': task_arn,
        'chromadb_host': private_ip,
        'chromadb_port': 8000
    }
```

**Fargate Task Definition**:
```dockerfile
# Dockerfile for ChromaDB server
FROM python:3.12-slim

RUN pip install chromadb boto3

COPY startup.py /app/startup.py

CMD ["python", "/app/startup.py"]
```

```python
# startup.py - Loads snapshot and starts server
import os
import boto3
import chromadb
from chromadb.config import Settings

# Download snapshot from S3
s3 = boto3.client('s3')
snapshot_path = os.environ['S3_SNAPSHOT_PATH']
local_path = '/data/chromadb'
# ... download and extract snapshot ...

# Start ChromaDB server
settings = Settings(
    persist_directory=local_path,
    anonymized_telemetry=False
)
client = chromadb.PersistentClient(settings=settings)

# Start HTTP server
chromadb.server.run(host="0.0.0.0", port=8000)
```

### 2. Parallel Query Steps

**Purpose**: Multiple Lambda functions query ChromaDB simultaneously

**Lambda Configuration**:
```python
# Each Lambda function uses the layer with chromadb-client
def query_vectors(event, context):
    # Get ChromaDB connection from Step Function input
    chromadb_host = event['chromadb_host']
    chromadb_port = event['chromadb_port']
    
    # Connect using chromadb-client from Lambda layer
    client = chromadb.HttpClient(
        host=chromadb_host,
        port=chromadb_port
    )
    
    # Perform vector queries
    collection = client.get_collection("receipts")
    results = collection.query(
        query_texts=["find similar receipts"],
        n_results=10
    )
    
    return {
        'query_results': results,
        'processed_count': len(results)
    }
```

**Key Points**:
- Lambda functions use the `receipt_label[lambda]` layer (33MB)
- ChromaDB client connects over HTTP to Fargate
- Multiple Lambdas can query simultaneously
- No need to download snapshots to Lambda

### 3. Cleanup Step (Stop Fargate)

**Purpose**: Terminate Fargate task and optionally save state

**Implementation**:
```python
def stop_chromadb_fargate(event, context):
    task_arn = event['task_arn']
    
    # Optionally trigger snapshot save before stopping
    if event.get('save_snapshot', False):
        chromadb_host = event['chromadb_host']
        # Trigger snapshot upload via API call
        # ...
    
    # Stop the Fargate task
    ecs_client.stop_task(
        cluster='my-cluster',
        task=task_arn,
        reason='Step Function workflow completed'
    )
    
    return {
        'status': 'cleaned_up',
        'task_arn': task_arn
    }
```

## Step Function Definition

```json
{
  "Comment": "ChromaDB query workflow with on-demand Fargate",
  "StartAt": "StartChromaDB",
  "States": {
    "StartChromaDB": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:region:account:function:start-chromadb-fargate",
      "Parameters": {
        "snapshot_path": "s3://bucket/chromadb/snapshot/latest/",
        "collection_name": "receipts"
      },
      "ResultPath": "$.chromadb",
      "Next": "ParallelQueries"
    },
    "ParallelQueries": {
      "Type": "Parallel",
      "Branches": [
        {
          "StartAt": "QuerySimilarReceipts",
          "States": {
            "QuerySimilarReceipts": {
              "Type": "Task",
              "Resource": "arn:aws:lambda:region:account:function:query-similar-receipts",
              "Parameters": {
                "chromadb_host.$": "$.chromadb.chromadb_host",
                "chromadb_port.$": "$.chromadb.chromadb_port",
                "query_text": "walmart groceries"
              },
              "End": true
            }
          }
        },
        {
          "StartAt": "QueryByMerchant",
          "States": {
            "QueryByMerchant": {
              "Type": "Task",
              "Resource": "arn:aws:lambda:region:account:function:query-by-merchant",
              "Parameters": {
                "chromadb_host.$": "$.chromadb.chromadb_host",
                "chromadb_port.$": "$.chromadb.chromadb_port",
                "merchant": "Target"
              },
              "End": true
            }
          }
        }
      ],
      "ResultPath": "$.query_results",
      "Next": "StopChromaDB"
    },
    "StopChromaDB": {
      "Type": "Task",
      "Resource": "arn:aws:lambda:region:account:function:stop-chromadb-fargate",
      "Parameters": {
        "task_arn.$": "$.chromadb.task_arn",
        "save_snapshot": false
      },
      "End": true
    }
  }
}
```

## Networking Configuration

### VPC Setup
```python
# Pulumi configuration
vpc = ec2.Vpc("chromadb-vpc",
    cidr_block="10.0.0.0/16",
    enable_dns_hostnames=True,
    enable_dns_support=True
)

private_subnet = ec2.Subnet("chromadb-private",
    vpc_id=vpc.id,
    cidr_block="10.0.1.0/24",
    availability_zone="us-east-1a"
)

# Security group for Fargate
chromadb_sg = ec2.SecurityGroup("chromadb-sg",
    vpc_id=vpc.id,
    ingress=[{
        "protocol": "tcp",
        "from_port": 8000,
        "to_port": 8000,
        "cidr_blocks": ["10.0.0.0/16"]  # Allow from VPC
    }],
    egress=[{
        "protocol": "-1",
        "from_port": 0,
        "to_port": 0,
        "cidr_blocks": ["0.0.0.0/0"]
    }]
)

# Lambda functions need VPC configuration
lambda_function = lambda_.Function("query-function",
    vpc_config={
        "subnet_ids": [private_subnet.id],
        "security_group_ids": [lambda_sg.id]
    },
    layers=[receipt_label_layer_arn],
    # ... other config
)
```

## Cost Analysis

### Traditional 24/7 Fargate
- **vCPU**: 0.5 vCPU = $18.40/month
- **Memory**: 2GB = $8.00/month
- **Storage**: 20GB = $2.00/month
- **Total**: ~$28.40/month minimum

### On-Demand Pattern
- **Assumptions**: 
  - 10 workflows per day
  - 5 minutes per workflow
  - 50 minutes total per day
- **vCPU**: 0.5 vCPU × 25 hours/month = $0.51
- **Memory**: 2GB × 25 hours/month = $0.22
- **Total**: ~$0.73/month (97% cost reduction!)

### Lambda Costs
- **With snapshot download**: ~$0.10 per GB downloaded
- **With Fargate**: ~$0.001 per query (just compute)

## Advantages

1. **Cost Effective**: Pay only during active workflows
2. **Scalable Queries**: Multiple Lambdas can query simultaneously
3. **Fast Queries**: No snapshot downloads, direct HTTP queries
4. **Isolation**: Each workflow gets fresh ChromaDB instance
5. **Simple Lambda Code**: Just HTTP client, no local DB management

## Disadvantages

1. **Startup Latency**: 30-60 seconds to start Fargate and load snapshot
2. **VPC Complexity**: Lambda functions need VPC configuration
3. **No Persistence**: Changes lost unless explicitly saved
4. **Single Point of Failure**: If Fargate crashes, all queries fail

## Alternative Patterns

### Pattern 1: EFS-Mounted ChromaDB
- Mount EFS to Lambda functions
- ChromaDB persists on EFS
- No Fargate needed
- Better for frequent, small queries

### Pattern 2: Batch Processing
- Stick with S3 snapshot + delta pattern
- Best for large batch operations
- Most cost-effective for infrequent access

### Pattern 3: Hybrid
- Use on-demand Fargate for complex workflows
- Use S3 snapshots for simple lookups
- Balance cost and performance

## Implementation Checklist

- [ ] Create Fargate task definition with ChromaDB
- [ ] Set up VPC and security groups
- [ ] Create start/stop Lambda functions
- [ ] Update Lambda functions to use chromadb-client layer
- [ ] Create Step Function state machine
- [ ] Add CloudWatch monitoring for Fargate task
- [ ] Implement error handling for Fargate failures
- [ ] Add automatic retry logic
- [ ] Create CloudWatch dashboard for costs
- [ ] Document query patterns and best practices

## Monitoring

### Key Metrics
- Fargate task startup time
- Query latency (P50, P95, P99)
- Number of concurrent queries
- Memory/CPU usage of Fargate task
- Cost per workflow
- Success/failure rates

### Alarms
- Fargate task fails to start
- Query timeout (>5 seconds)
- High memory usage (>80%)
- Excessive concurrent workflows

## Security Considerations

1. **Network Isolation**: Fargate and Lambda in private subnets
2. **No Internet Access**: Use VPC endpoints for S3
3. **IAM Roles**: Minimal permissions for each component
4. **Encryption**: S3 snapshots encrypted at rest
5. **No Persistent State**: Reduces attack surface

## Future Enhancements

1. **Connection Pooling**: Reuse Fargate task across workflows
2. **Auto-scaling**: Multiple Fargate tasks for high load
3. **Caching Layer**: Redis/ElastiCache for frequent queries
4. **GraphQL API**: Add AppSync for client queries
5. **Multi-region**: Replicate snapshots for DR

## Conclusion

This on-demand Fargate pattern provides an excellent balance between cost and functionality for ChromaDB vector queries in Step Functions. It's particularly well-suited for:

- Complex analytical workflows
- Batch processing with vector similarity
- Exploratory data analysis
- One-off large-scale queries

The 97% cost reduction compared to 24/7 operation makes it viable for projects with budget constraints while maintaining the full power of ChromaDB's vector search capabilities.