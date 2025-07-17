# Validation Pipeline CRUD Operations Documentation

This document provides a comprehensive overview of the CRUD (Create, Read, Update, Delete) operations for DynamoDB and Pinecone within the StepFunction validation pipeline.

## Overview

The validation pipeline consists of two main StepFunctions:
1. **Submit Completion Batch** - Processes receipts for validation
2. **Poll Completion Batch** - Monitors and updates validation results

These StepFunctions orchestrate Lambda functions that perform various CRUD operations on DynamoDB and Pinecone.

## DynamoDB CRUD Operations

### Table Configuration
- **Table Name**: `ReceiptsTable`
- **Primary Key**: 
  - Partition Key (PK): `IMAGE#{image_id}`
  - Sort Key (SK): Various patterns for different entities
- **Global Secondary Indexes**:
  - **GSI1**: Merchant-based queries (GSI1PK/GSI1SK)
  - **GSI2**: Place ID queries (GSI2PK/GSI2SK)
  - **GSI3**: Validation status queries (GSI3PK/GSI3SK)
  - **GSITYPE**: Entity type queries (TYPE)
- **Billing Mode**: PAY_PER_REQUEST (on-demand)
- **Features**: TTL enabled, Point-in-time recovery, DynamoDB Streams

### Entity Hierarchy
```
IMAGE#{image_id}
├── RECEIPT#{receipt_id}
│   ├── METADATA
│   ├── LINE#{line_id}
│   │   └── WORD#{word_id}
│   │       └── LETTER#{letter_id}
│   └── WORD_LABEL#{line_id}#{word_id}
└── BATCH_SUMMARY#{batch_id}
```

### CRUD Operations by Lambda Function

#### 1. **submit_list_handler** (lambda.py:59-74)
**Operation**: READ
- **Purpose**: List labels that need validation
- **DynamoDB Actions**:
  - Query GSITYPE index where TYPE = 'WORD_LABEL'
  - Filter for labels with validation_status = 'pending'
- **Implementation**: `list_labels_that_need_validation()`

#### 2. **submit_format_handler** (lambda.py:77-102)
**Operation**: READ
- **Purpose**: Retrieve receipt details for batch file generation
- **DynamoDB Actions**:
  - Get receipt metadata: `get_item(PK='IMAGE#{image_id}', SK='RECEIPT#{receipt_id}#METADATA')`
  - Query for lines: `query(PK='IMAGE#{image_id}', SK begins_with 'RECEIPT#{receipt_id}#LINE#')`
  - Query for words: `query(PK='IMAGE#{image_id}', SK begins_with 'RECEIPT#{receipt_id}#LINE#{line_id}#WORD#')`
  - Get existing labels: `batch_get_item()` for WORD_LABEL entities
- **Implementation**: `get_receipt_details(image_id, receipt_id)`

#### 3. **submit_openai_handler** (lambda.py:104-154)
**Operations**: CREATE, UPDATE
- **Purpose**: Submit batches to OpenAI and track status
- **DynamoDB Actions**:
  - **CREATE**: 
    - Put new batch summary: `put_item()` with BatchSummary entity
    - Key: `PK='BATCH_SUMMARY', SK='BATCH#{batch_id}'`
  - **UPDATE**:
    - Update label validation status to 'validating'
    - `update_item()` on WORD_LABEL entities
- **Implementation**: 
  - `add_batch_summary()`
  - `update_label_validation_status()`

#### 4. **poll_list_handler** (lambda.py:157-167)
**Operation**: READ
- **Purpose**: List pending completion batches
- **DynamoDB Actions**:
  - Query for BatchSummary entities with status = 'pending'
  - Uses GSI3 for efficient querying by status
- **Implementation**: `list_pending_completion_batches()`

#### 5. **poll_download_handler** (lambda.py:170-194)
**Operations**: UPDATE
- **Purpose**: Process completed batches and update validation results
- **DynamoDB Actions**:
  - **UPDATE pending labels**: Set validation_status = 'valid/invalid'
  - **UPDATE valid labels**: Add valid_label metadata
  - **UPDATE invalid labels**: Add invalid_reason metadata
  - **UPDATE batch summary**: Set status = 'completed'
- **Implementation**:
  - `update_pending_labels()`
  - `update_valid_labels()`
  - `update_invalid_labels()`
  - `update_batch_summary()`

### Base CRUD Operations (receipt_dynamo package)

#### SingleEntityCRUDMixin
- **Create**: `_add_entity()` - Uses `put_item()` with `attribute_not_exists(PK)` condition
- **Read**: Implemented via query operations
- **Update**: `_update_entity()` - Uses `put_item()` with `attribute_exists(PK)` condition
- **Delete**: `_delete_entity()` - Uses `delete_item()` with `attribute_exists(PK)` condition

#### BatchOperationsMixin
- **Batch Write**: `_batch_write_with_retry()`
  - Chunks items into batches of 25 (DynamoDB limit)
  - Implements retry logic for unprocessed items
  - Uses `batch_write_item()` API

#### TransactionalOperationsMixin
- **Transactional Write**: `_transact_write_with_chunking()`
  - Supports atomic multi-item operations
  - Automatic chunking for large transactions

## Pinecone CRUD Operations

### Index Configuration
- **Index Name**: Configured via environment variables
- **Host**: Configured via environment variables
- **Namespaces**: 
  - `words` - Word-level embeddings
  - `lines` - Line-level embeddings

### Vector Metadata Schema
```json
{
  "image_id": "string",
  "receipt_id": "number",
  "line_id": "number",
  "word_id": "number",
  "text": "string",
  "merchant_name": "string",
  "valid_labels": ["array", "of", "labels"],
  "confidence": "number",
  "validation_status": "string"
}
```

### CRUD Operations in Validation Pipeline

#### 1. **Embedding Creation** (Not in StepFunction, but referenced)
**Operation**: CREATE
- **Purpose**: Store word and line embeddings
- **Pinecone Actions**:
  - Batch upsert: `pinecone.upsert(vectors=chunk, namespace="words")`
  - Typical batch size: 100 vectors
- **Vector ID Format**: `{image_id}_{receipt_id}_{line_id}_{word_id}`

#### 2. **Label Validation Queries**
**Operation**: READ
- **Purpose**: Find similar vectors for validation
- **Pinecone Actions**:
  - Query by vector similarity: `pinecone.query(vector=embedding, top_k=10, filter={...})`
  - Fetch specific vectors: `pinecone.fetch(ids=[...], namespace="words")`
- **Filters**:
  - `valid_labels`: Filter by label types
  - `merchant_name`: Filter by merchant name
  - `validation_status`: Filter by validation status

#### 3. **Metadata Updates**
**Operation**: UPDATE
- **Purpose**: Update vector metadata after validation
- **Pinecone Actions**:
  - Update metadata: `pinecone.update(id=vector_id, set_metadata={...}, namespace="words")`
- **Updated Fields**:
  - `validation_status`: 'pending' → 'valid'/'invalid'
  - `valid_labels`: Add newly validated labels
  - `confidence`: Update confidence scores

#### 4. **Vector Deletion**
**Operation**: DELETE (Not implemented)
- The system does not currently delete vectors from Pinecone
- Vectors are retained for historical analysis and improving validation

### Integration Points

#### DynamoDB → Pinecone Sync
1. When labels are validated in DynamoDB, corresponding Pinecone vectors are updated
2. Metadata in Pinecone mirrors validation status in DynamoDB
3. Batch operations ensure consistency between systems

#### Pinecone → DynamoDB Validation
1. Pinecone queries inform validation decisions
2. Similar vectors help validate new labels
3. Results are written back to DynamoDB

## StepFunction Orchestration

### Submit Completion Batch Flow
```
1. submit_list_handler (READ from DynamoDB)
   ↓
2. submit_format_handler (READ from DynamoDB) [Map - parallel]
   ↓
3. submit_openai_handler (CREATE/UPDATE in DynamoDB)
```

### Poll Completion Batch Flow
```
1. poll_list_handler (READ from DynamoDB)
   ↓
2. poll_download_handler (UPDATE in DynamoDB/Pinecone) [Map - parallel]
```

## Error Handling and Resilience

### DynamoDB
- Automatic retries with exponential backoff
- Batch operations handle partial failures
- Conditional expressions prevent race conditions
- Circuit breakers for high-volume operations

### Pinecone
- Chunking to respect API limits (100 vectors/request)
- Retry logic for transient failures
- Metadata validation before updates
- Namespace isolation for data separation

## Performance Optimizations

### DynamoDB
- GSI usage for efficient queries
- Batch operations to reduce API calls
- Projection expressions to minimize data transfer
- PAY_PER_REQUEST for automatic scaling

### Pinecone
- Batch upserts for efficiency
- Metadata filtering to reduce result sets
- Appropriate top_k values for queries
- Namespace separation for faster queries

## Security Considerations

1. **AWS IAM Roles**: Lambda functions have minimal required permissions
2. **API Keys**: Stored in environment variables, managed by AWS Secrets Manager
3. **Data Validation**: All inputs are validated before operations
4. **Error Messages**: Sanitized to prevent information leakage

## Monitoring and Observability

- CloudWatch Logs for all Lambda executions
- StepFunction execution history
- DynamoDB CloudWatch metrics
- Custom metrics for validation success/failure rates

## Future Enhancements

1. **Implement Pinecone vector deletion** for data lifecycle management
2. **Add DynamoDB Streams** integration for real-time Pinecone updates
3. **Implement caching layer** for frequently accessed data
4. **Add transaction support** for cross-system consistency
5. **Enhance monitoring** with custom dashboards and alerts