# Word Embedding Module

This module handles the generation of embeddings for individual receipt words using OpenAI's Batch API. Word embeddings capture contextual information about each word including spatial relationships and neighboring text.

## Purpose

Word embeddings are used for:
- Word-level label validation
- Semantic search within receipts
- Finding similar words across different receipts
- Training and improving label classification models

## Key Features

### Contextual Embedding
Each word embedding includes:
- **Target word**: The word being embedded
- **3x3 Grid context**: Words in surrounding positions (top/middle/bottom, left/center/right)
- **Horizontal context**: Words immediately to the left and right
- **Formatted input**: Special tokens like `<TARGET>`, `<POS>`, and `<CONTEXT>`

### Rich Metadata
Embeddings are stored in Pinecone with extensive metadata:
- **Spatial data**: x, y coordinates, width, height, angle
- **OCR confidence**: Word and character-level confidence scores
- **Labels**: Valid and invalid label lists, proposed labels
- **Receipt context**: Merchant name, receipt/image IDs
- **Processing status**: Embedding status, source information

## Architecture Details

### Submit Pipeline (`submit.py`)
1. Lists words with `embedding_status = NONE`
2. Chunks by receipt for efficient processing
3. Formats contextual embeddings with spatial relationships
4. Submits to OpenAI Batch API (24-hour completion)
5. Updates word status to PENDING

### Poll Pipeline (`poll.py`)
1. Lists pending embedding batches
2. Downloads completed results from OpenAI
3. Enriches with full receipt context from DynamoDB
4. Upserts to Pinecone namespace "words"
5. Updates embedding status to SUCCESS

## Integration Points

### Pinecone Configuration
- **Namespace**: "words" (separate from line embeddings)
- **Vector dimension**: Matches OpenAI's text-embedding-3-small output
- **Metadata indices**: Optimized for filtering by merchant, labels, confidence

### DynamoDB Entities
- `ReceiptWord`: Source data with embedding_status field
- `BatchSummary`: Tracks batch jobs with type "EMBEDDING"
- `EmbeddingBatchResult`: Audit trail of processed embeddings

### OpenAI Batch API
- **Model**: text-embedding-3-small
- **Batch size**: Limited by 50k lines or 100MB
- **Format**: NDJSON with custom_id for tracking

## Common Patterns

### Custom ID Format
```
IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}
```

### Embedding Input Format
```
<TARGET>word_text</TARGET> <POS>grid_position</POS> word1 word2 word3 word4 word5 word6 word7 word8 <CONTEXT>left_word right_word</CONTEXT>
```

## Debugging Tips

### Missing Embeddings
1. Check word's embedding_status in DynamoDB
2. Verify BatchSummary exists and status
3. Check OpenAI batch status for failures
4. Validate NDJSON format in S3 files

### Metadata Issues
1. Ensure all required fields are present
2. Check for special characters in text
3. Verify coordinate calculations
4. Validate label format (lists vs strings)

### Performance Optimization
- Batch words by receipt to minimize DynamoDB queries
- Use bulk upsert operations for Pinecone
- Process results in parallel where possible
- Cache receipt details during processing

## Testing Considerations
- Mock OpenAI responses with realistic embeddings
- Test edge cases: empty context, single-word lines
- Validate coordinate calculations
- Ensure proper handling of special characters
- Test batch size limits