# Metadata Harmonizer Step Function

## Purpose

Ensures metadata consistency across receipts sharing the same Google Place ID using an LLM agent (`MerchantHarmonizerV3`) for intelligent reasoning about edge cases.

## Architecture

- **Zip Lambda**: `list_place_ids` - Scans DynamoDB for unique place_ids and batches them
- **Container Lambda**: `harmonize_metadata` - Runs `MerchantHarmonizerV3` agent for each batch
- **Step Function**: Orchestrates the workflow with Map state for parallel processing
- **S3 Bucket**: Stores batch files and results (optional, for future use)

## Workflow

1. **ListPlaceIds**: Query DynamoDB for all unique place_ids and group into batches
2. **ProcessInBatches**: Map over batches, running harmonization for each batch in parallel
3. **Done**: Aggregate results

## Usage

### Input

```json
{
  "dry_run": true,
  "langchain_project": "metadata-harmonizer"
}
```

### Output

The Step Function returns a summary of processing results, with detailed results stored in CloudWatch Logs.

## Configuration

Pulumi config keys (under `metadata-harmonizer` namespace):
- `max_concurrency`: Maximum parallel batches (default: 5)
- `batch_size`: Place IDs per batch (default: 10)

## Environment Variables

The container Lambda requires:
- `RECEIPT_AGENT_DYNAMO_TABLE_NAME`: DynamoDB table name
- `RECEIPT_AGENT_OPENAI_API_KEY`: OpenAI API key (for agent LLM)
- `RECEIPT_AGENT_OLLAMA_API_KEY`: Ollama API key (for agent LLM)
- `GOOGLE_PLACES_API_KEY`: Google Places API key (optional, but recommended)
- `LANGCHAIN_API_KEY`: LangSmith API key (for tracing)

## How It Works

1. **List Place IDs**: The zip Lambda scans all receipt metadatas in DynamoDB and extracts unique place_ids, grouping them into batches.

2. **Harmonize Batch**: For each batch of place_ids:
   - Loads all receipts from DynamoDB
   - Groups receipts by place_id
   - Filters to only inconsistent groups (skips already consistent ones)
   - Runs the LLM agent for each inconsistent group
   - Applies updates to DynamoDB (if not dry_run)

3. **Agent Processing**: The `MerchantHarmonizerV3` agent:
   - Examines all receipts in a place_id group
   - Validates against Google Places API (source of truth)
   - Reasons about edge cases (OCR errors, address-like names)
   - Determines canonical values for merchant_name, address, phone
   - Identifies which receipts need updates

## Differences from Label Harmonizer

- **No ChromaDB**: Metadata harmonizer only needs DynamoDB and optional Google Places API
- **Simpler workflow**: No parallel prepare step, just list → process
- **Place ID based**: Groups by place_id instead of merchant_name + label_type
- **Agent-based**: Uses LLM agent for intelligent reasoning (vs. similarity search)

## Monitoring

- CloudWatch Logs: `/aws/stepfunctions/metadata-harmonizer-{stack}-sf`
- LangSmith: Traces for each agent invocation (if enabled)
- EMF Metrics: PlaceIdsProcessed, GroupsProcessed, ReceiptsUpdated, ProcessingTimeSeconds


