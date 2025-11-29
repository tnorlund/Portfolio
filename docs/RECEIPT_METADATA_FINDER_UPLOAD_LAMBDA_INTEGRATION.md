# Receipt Metadata Finder Agent - Upload Lambda Integration

## Overview

The receipt metadata finder agent has been integrated into the upload lambda workflow to automatically find ALL missing metadata fields (place_id, merchant_name, address, phone_number) for receipts during the upload process.

## Integration Details

### Workflow

1. **Initial Metadata Creation** (existing)
   - The upload lambda processes OCR results
   - Creates initial `ReceiptMetadata` using `create_receipt_metadata_simple()` workflow
   - This may create metadata with only merchant_name, missing place_id, address, or phone_number

2. **Metadata Finder Agent** (new)
   - After initial metadata creation, the `MetadataFinderProcessor` runs
   - Checks if any metadata fields are missing
   - Uses the receipt metadata finder agent to find missing fields using:
     - Receipt content analysis (lines, words, labels)
     - Google Places API search
     - Similar receipts from ChromaDB
     - Agent-based reasoning
   - Updates `ReceiptMetadata` with any found fields

### Files Modified

1. **`infra/upload_images/container_ocr/handler/metadata_finder_processor.py`** (new)
   - `MetadataFinderProcessor` class that:
     - Sets up ChromaDB client (EFS → S3 → HTTP fallback)
     - Creates embedding function for ChromaDB queries
     - Runs the receipt metadata finder agent workflow
     - Updates ReceiptMetadata with found fields

2. **`infra/upload_images/container_ocr/handler/embedding_processor.py`** (modified)
   - Added call to `MetadataFinderProcessor` after initial metadata creation
   - Wrapped in try/except to not fail the whole process if finder fails
   - Logs results for monitoring

3. **`infra/upload_images/container_ocr/Dockerfile`** (modified)
   - Added `receipt_agent` and `receipt_places` packages to dependencies
   - Required for the metadata finder agent to work

### Key Features

- **Non-blocking**: If the metadata finder fails, it doesn't fail the entire upload process
- **Efficient**: Only runs if metadata has missing fields
- **Comprehensive**: Finds ALL missing fields, not just place_id
- **Intelligent**: Uses agent-based reasoning to extract metadata from receipt content even if Google Places fails
- **Partial fills**: Can fill in some fields even if others can't be found

### Error Handling

- If ChromaDB is unavailable, the finder is skipped (non-critical)
- If the agent fails, it's logged but doesn't fail the upload
- If all fields are already present, the finder is skipped (efficient)

### Configuration

The metadata finder uses the same environment variables as the embedding processor:
- `DYNAMO_TABLE_NAME`: DynamoDB table name
- `CHROMADB_BUCKET`: S3 bucket for ChromaDB snapshots
- `CHROMA_HTTP_ENDPOINT`: Optional HTTP endpoint for ChromaDB
- `GOOGLE_PLACES_API_KEY`: Google Places API key
- `OPENAI_API_KEY`: OpenAI API key for embeddings
- `OLLAMA_API_KEY`: Ollama API key for agent
- `LANGCHAIN_API_KEY`: LangSmith API key for tracing

### Monitoring

The metadata finder logs:
- When it runs and why (missing fields detected)
- How many fields were found
- How many fields were updated
- Any errors (non-critical)

Look for `[METADATA_FINDER]` prefix in CloudWatch logs.

## Example Flow

1. Receipt uploaded → OCR processed
2. Initial metadata created: `merchant_name="Starbucks"`, `place_id=""`, `address=""`, `phone_number=""`
3. Metadata finder runs:
   - Detects missing fields: `place_id`, `address`, `phone_number`
   - Agent searches Google Places API
   - Agent extracts from receipt content
   - Agent finds: `place_id="ChIJ...", address="123 Main St", phone_number="(555) 123-4567"`
4. ReceiptMetadata updated with all fields
5. Embeddings created with complete metadata

## Benefits

1. **Complete metadata**: All fields filled automatically during upload
2. **Better search**: Receipts with place_id can be found by location
3. **Better UX**: Users see complete merchant information
4. **Reduced manual work**: No need to run separate metadata finder scripts

## Future Improvements

- Add metrics for metadata finder success rate
- Add configuration to enable/disable finder per environment
- Add retry logic for transient failures
- Cache ChromaDB snapshots more efficiently


