# receipt_agent

🤖 **Agentic validation for receipt metadata using LangGraph and ChromaDB**

`receipt_agent` provides an intelligent agent that validates receipt merchant metadata by:
- Searching ChromaDB for similar receipts
- Cross-referencing merchant data across receipts
- Verifying consistency of place IDs, addresses, and phone numbers
- Optionally checking Google Places for ground truth
- Making reasoned validation decisions with full traceability

## Features

- 🔍 **Agentic Search**: Uses LangGraph to orchestrate multi-step validation workflows
- 📊 **ChromaDB Integration**: Similarity search across receipt embeddings
- 🏪 **Merchant Validation**: Cross-reference validation against other receipts
- 🌍 **Google Places Verification**: Optional verification against Google Places API
- 📈 **LangSmith Tracing**: Full observability with LangSmith integration
- ☁️ **Ollama Cloud**: Uses Ollama Cloud for LLM reasoning
- 🔧 **Typed State**: Pydantic models for type-safe state management

## Installation

```bash
# From the Portfolio root
cd receipt_agent
pip install -e ".[dev]"

# Or with uv
uv pip install -e ".[dev]"
```

## Quick Start

```python
import asyncio
from receipt_agent import MetadataValidatorAgent
from receipt_agent.clients import create_all_clients

async def main():
    # Create all clients with proper caching configuration
    clients = create_all_clients()

    # Create the validation agent
    agent = MetadataValidatorAgent(
        dynamo_client=clients["dynamo_client"],
        chroma_client=clients["chroma_client"],
        places_api=clients["places_api"],  # Includes DynamoDB caching!
        embed_fn=clients["embed_fn"],
    )

    # Validate a single receipt
    result = await agent.validate(
        image_id="abc-123-def",
        receipt_id=1,
    )

    print(f"Status: {result.status}")
    print(f"Confidence: {result.confidence:.2f}")
    print(f"Reasoning: {result.reasoning}")

    # Check recommendations
    for rec in result.recommendations:
        print(f"  - {rec}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Manual Client Creation

```python
from receipt_agent.clients import (
    create_dynamo_client,
    create_chroma_client,
    create_places_api,
    create_embed_fn,
)

# Create individual clients
dynamo = create_dynamo_client(table_name="my-table")
chroma = create_chroma_client(persist_directory="/path/to/chroma")

# Places API with DynamoDB caching (IMPORTANT for cost optimization!)
places = create_places_api(
    api_key="your-google-key",
    dynamo_client=dynamo,  # Enables caching
)
```

## Configuration

Configure via environment variables (prefix: `RECEIPT_AGENT_`):

```bash
# LLM Configuration (Ollama Cloud)
export RECEIPT_AGENT_OLLAMA_BASE_URL="https://api.ollama.ai"
export RECEIPT_AGENT_OLLAMA_API_KEY="your-ollama-key"
export RECEIPT_AGENT_OLLAMA_MODEL="llama3.1:8b"

# Embeddings (OpenAI)
export RECEIPT_AGENT_OPENAI_API_KEY="your-openai-key"
export RECEIPT_AGENT_EMBEDDING_MODEL="text-embedding-3-small"

# ChromaDB
export RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY="/path/to/chroma"

# DynamoDB
export RECEIPT_AGENT_DYNAMO_TABLE_NAME="receipts"
export RECEIPT_AGENT_AWS_REGION="us-west-2"

# Google Places (optional)
export RECEIPT_AGENT_GOOGLE_PLACES_API_KEY="your-google-key"

# LangSmith Tracing
export LANGCHAIN_API_KEY="your-langsmith-key"
export LANGCHAIN_PROJECT="receipt-agent"
export LANGCHAIN_TRACING_V2="true"

# Agent Settings
export RECEIPT_AGENT_MAX_ITERATIONS="10"
export RECEIPT_AGENT_SIMILARITY_THRESHOLD="0.75"
export RECEIPT_AGENT_MIN_MATCHES_FOR_VALIDATION="3"
```

## Architecture

### Workflow Graph

```
┌─────────────────┐
│  load_metadata  │  ← Entry: Load current metadata from DynamoDB
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ search_similar  │  ← Search ChromaDB for similar receipts
└────────┬────────┘
         │
         ▼
┌──────────────────────┐
│ verify_consistency   │  ← Check consistency across merchant receipts
└────────┬─────────────┘
         │
         ├────────────────────┐
         ▼                    ▼
┌─────────────────┐   ┌──────────────┐
│  check_places   │   │ make_decision│  ← Skip if high confidence
│   (optional)    │   └──────┬───────┘
└────────┬────────┘          │
         │                   │
         └───────────────────┘
                   │
                   ▼
              ┌─────────┐
              │   END   │  ← Return ValidationResult
              └─────────┘
```

### State Model

```python
class ValidationState:
    # Input
    image_id: str
    receipt_id: int

    # Current metadata from DynamoDB
    current_merchant_name: str
    current_place_id: str
    current_address: str
    current_phone: str

    # ChromaDB search results
    chroma_line_results: list[ChromaSearchResult]
    merchant_candidates: list[MerchantCandidate]

    # Verification progress
    verification_steps: list[VerificationStep]

    # Final output
    result: ValidationResult
```

### Validation Result

```python
class ValidationResult:
    status: ValidationStatus  # VALIDATED, INVALID, NEEDS_REVIEW, PENDING, ERROR
    confidence: float         # 0.0 to 1.0
    validated_merchant: MerchantCandidate
    verification_steps: list[VerificationStep]
    evidence_summary: list[VerificationEvidence]
    reasoning: str            # LLM-generated explanation
    recommendations: list[str]
```

## Available Tools

The agent uses these tools during validation:

### ChromaDB Tools
- `query_similar_lines` - Find receipts with similar text lines
- `query_similar_words` - Find similar labeled words
- `search_by_merchant_name` - Find all receipts from a merchant
- `search_by_place_id` - Find receipts by Google Place ID

### DynamoDB Tools
- `get_receipt_metadata` - Get current metadata
- `get_receipt_context` - Get receipt lines and words
- `get_receipts_by_merchant` - Find other receipts from same merchant

### Google Places Tools
- `verify_with_google_places` - Verify against Google Places API
- `compare_metadata_with_places` - Compare current vs Google data

## Batch Validation

```python
async def batch_validate():
    agent = MetadataValidatorAgent(...)

    # List of receipts to validate
    receipts = [
        ("image-1", 1),
        ("image-2", 1),
        ("image-3", 2),
    ]

    # Validate with concurrency limit
    results = await agent.validate_batch(
        receipts=receipts,
        max_concurrency=5,
    )

    for (image_id, receipt_id), result in results:
        print(f"{image_id}#{receipt_id}: {result.status.value}")
```

## LangSmith Tracing

All validation runs are automatically traced to LangSmith:

```python
from receipt_agent.tracing import ValidationRunContext

async with ValidationRunContext(
    image_id=image_id,
    receipt_id=receipt_id,
    tags=["production", "batch-validation"],
) as ctx:
    result = await agent.validate(image_id, receipt_id)

    # Get the LangSmith URL for this run
    print(f"View run: {ctx.get_run_url()}")

    # Log user feedback
    ctx.log_feedback(score=1.0, comment="Validation was accurate")
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=receipt_agent

# Type checking
mypy receipt_agent

# Linting
ruff check receipt_agent
ruff format receipt_agent
```

## Example: Custom Embedding Function

```python
# Use a custom embedding function instead of OpenAI
def my_embed_fn(texts: list[str]) -> list[list[float]]:
    # Your embedding logic here
    ...

agent = MetadataValidatorAgent(
    dynamo_client=dynamo,
    chroma_client=chroma,
    embed_fn=my_embed_fn,
)
```

## Example: Validation with Places API (Cached)

```python
from receipt_agent.clients import create_places_api, create_dynamo_client

# IMPORTANT: Create Places API with DynamoDB caching to minimize costs!
dynamo = create_dynamo_client()
places_api = create_places_api(
    api_key="your-google-key",
    dynamo_client=dynamo,  # Enables PlacesCache in DynamoDB
)

agent = MetadataValidatorAgent(
    dynamo_client=dynamo,
    chroma_client=chroma,
    places_api=places_api,  # Uses cached responses (30-day TTL)
)

# Validation will check DynamoDB cache before making Places API calls
# Expected cache hit rates: 70-90% for phone, 40-60% for address
result = await agent.validate(image_id, receipt_id)
```

### Cost Optimization

The `PlacesCache` in DynamoDB significantly reduces API costs:

| Scenario | Places API Calls | Cost (100 validations) |
|----------|------------------|------------------------|
| Cold cache | 2-3 per validation | ~$3.40 |
| 70% cache hit | 0.6-0.9 per validation | ~$1.02 |
| Warm cache | 0-0.3 per validation | ~$0.30 |

See [docs/ACCESS_PATTERNS.md](docs/ACCESS_PATTERNS.md) for detailed cost analysis.

## Validation Statuses

| Status | Description |
|--------|-------------|
| `VALIDATED` | Metadata confirmed accurate (high confidence) |
| `INVALID` | Metadata appears incorrect (needs correction) |
| `NEEDS_REVIEW` | Inconclusive - manual review recommended |
| `PENDING` | Insufficient data for determination |
| `ERROR` | Validation failed due to error |

## Dependencies

- `langgraph>=0.2.0` - Workflow orchestration
- `langchain-ollama>=0.2.0` - Ollama Cloud integration
- `langsmith>=0.1.0` - Tracing and observability
- `chromadb>=0.5.0` - Vector similarity search
- `pydantic>=2.0.0` - State models
- `receipt_dynamo` - DynamoDB client (local package)
- `receipt_chroma` - ChromaDB client (local package)

## License

MIT

