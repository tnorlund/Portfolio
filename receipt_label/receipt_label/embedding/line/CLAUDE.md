# Receipt Label Embedding Module

This module handles the generation of embeddings for receipt text using OpenAI's Batch API. It supports both word-level and line-level embeddings for different use cases.

## Module Structure

The embedding module is divided into two sub-modules:

### 1. Word Embeddings (`word/`)
**Purpose**: Generate embeddings for individual words with contextual information
**Use Case**: Word-level labeling and classification
**Pinecone Namespace**: "words"

### 2. Line Embeddings (`line/`)
**Purpose**: Generate embeddings for entire lines of text
**Use Case**: Receipt section classification (header, items, totals, etc.)
**Pinecone Namespace**: "lines"

## Architecture Overview

Both embedding types follow the same two-pipeline pattern:

### Submit Pipeline
1. **List**: Find entities needing embeddings (embedding_status=NONE)
2. **Chunk**: Group by receipt for efficient processing
3. **Serialize**: Convert to NDJSON and upload to S3
4. **Format**: Create OpenAI-compatible embedding requests
5. **Submit**: Upload to OpenAI and submit batch job
6. **Track**: Create BatchSummary with status=PENDING

### Poll Pipeline
1. **List**: Find pending batches
2. **Check**: Poll OpenAI for batch completion
3. **Download**: Retrieve embedding results
4. **Enrich**: Fetch full receipt context from DynamoDB
5. **Store**: Upsert to Pinecone with rich metadata
6. **Update**: Mark entities as SUCCESS and batch as COMPLETED

## Infrastructure Integration

The embedding pipelines are deployed as AWS Step Functions in two locations:

1. **LineEmbeddingStepFunction** (`infra/embedding_step_functions/`)
   - Modern implementation for line embeddings
   - Uses `infra.py` and `lambda.py`

2. **WordLabelStepFunctions** (`infra/word_label_step_functions/`)
   - Handles both word and line embeddings
   - Appears to be an earlier implementation

## Key Implementation Details

### Word Embeddings
- Include 3x3 spatial context (top/middle/bottom, left/center/right)
- Capture neighboring words for context
- Store label validation status in metadata

### Line Embeddings
- Process entire line text
- Include previous/next line context
- Store section labels when available
- Track spatial information (x, y, width, height)

### Common Features
- Batch processing via OpenAI Batch API (24h completion window)
- S3 for intermediate file storage
- DynamoDB for state management
- Pinecone for vector storage and search
- Rich metadata for filtering and analysis

## Common Tasks

### Add New Embedding Type
1. Create new directory under `embedding/`
2. Implement `submit.py` and `poll.py` following existing patterns
3. Define unique Pinecone namespace
4. Add infrastructure in `infra/`

### Debug Embedding Issues
1. Check BatchSummary status in DynamoDB
2. Verify OpenAI batch status
3. Check for timeout issues
4. Validate NDJSON format
5. Ensure Pinecone namespace is correct

### Modify Embedding Context
- Word: Update `format_word_context_embedding()` in word/submit.py
- Line: Update `format_line_context_embedding()` in line/submit.py

## Testing Considerations
- Mock OpenAI API responses
- Test batch chunking logic
- Validate Pinecone metadata structure
- Ensure proper error handling for failed batches

## Performance Notes
- OpenAI Batch API has 24-hour SLA
- Batches limited to 50k lines or 100MB
- Pinecone upserts in chunks of 100 vectors
- DynamoDB updates in chunks of 25 items