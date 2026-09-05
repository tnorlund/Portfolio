# receipt_agent

🤖 **LangGraph agents for receipt analysis**

`receipt_agent` hosts the LangGraph agents that reason over receipt data
stored in DynamoDB:

- `agents/question_answering` — the QA agent that answers marquee questions
  about receipt data (plan → agent → tools → shape → synthesize)
- `agents/label_evaluator` — structured financial / currency / metadata
  review of word labels
- `agents/place_id_finder` and `subagents/place_finder` — Google Place ID
  resolution for receipts with missing or wrong merchant metadata
- `agents/receipt_grouping` — clustering of receipts by merchant
- `subagents/financial_validation`, `subagents/table_columns` — focused
  structured-output helpers used by the evaluators

Vector similarity search is provided by `receipt_embeddings`
(`DynamoVectorSearchClient`), which queries the native DynamoDB embedding
items. There is no separate vector database.

## Features

- 🔍 **Agentic Search**: Uses LangGraph to orchestrate multi-step workflows
- 📊 **Vector Similarity**: DynamoDB-native embedding search via
  `receipt_embeddings`
- 🌍 **Google Places Verification**: Optional verification against the
  Google Places API, cached in DynamoDB via `receipt_places`
- 📈 **LangSmith Tracing**: Full observability with LangSmith integration
- ☁️ **OpenRouter**: Uses OpenRouter for LLM reasoning
- 🔧 **Typed State**: Pydantic models for type-safe state management

## Installation

```bash
# From the Portfolio root
pip install -e receipt_dynamo -e receipt_embeddings
pip install --no-deps -e receipt_places -e receipt_agent
pip install -e "receipt_agent[dev]"
```

## Quick Start

```python
from receipt_agent.agents.question_answering import create_qa_graph
from receipt_agent.clients import create_dynamo_client, create_embed_fn

dynamo = create_dynamo_client(table_name="my-table")
embed_fn = create_embed_fn()

graph, _state_holder = create_qa_graph(dynamo_client=dynamo, embed_fn=embed_fn)
result = graph.invoke({"question": "How much did I spend at Sprouts?"})
print(result["final_answer"])
```

### Client Factory

```python
from receipt_agent.clients import (
    create_all_clients,
    create_dynamo_client,
    create_embed_fn,
    create_places_api,
)

clients = create_all_clients()  # dynamo_client, places_api, embed_fn

# Places API with DynamoDB caching (IMPORTANT for cost optimization!)
places = create_places_api(
    api_key="your-google-key",
    dynamo_client=clients["dynamo_client"],  # Enables caching
)
```

## Configuration

Configure via environment variables (prefix: `RECEIPT_AGENT_`):

```bash
# LLM Configuration (OpenRouter)
export OPENROUTER_API_KEY="your-openrouter-key"
export OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
export OPENROUTER_MODEL="openai/gpt-oss-120b"

# Embeddings (OpenAI)
export RECEIPT_AGENT_OPENAI_API_KEY="your-openai-key"
export RECEIPT_AGENT_EMBEDDING_MODEL="text-embedding-3-small"

# DynamoDB (also read by receipt_embeddings' DynamoVectorSearchClient)
export RECEIPT_AGENT_DYNAMO_TABLE_NAME="receipts"
export DYNAMODB_TABLE_NAME="receipts"
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

## Available Tools

### Vector similarity (via `receipt_embeddings`)
- `search_receipts` / `search_product_lines` — semantic search over line
  embeddings
- `similar_labeled_words` — nearest labeled words for a target word

### DynamoDB Tools
- `get_receipt_metadata` - Get current metadata
- `get_receipt_context` - Get receipt lines and words
- `get_receipts_by_merchant` - Find other receipts from same merchant

### Google Places Tools
- `verify_with_google_places` - Verify against Google Places API
- `compare_metadata_with_places` - Compare current vs Google data

## LangSmith Tracing

All agent runs are traced to LangSmith when `LANGCHAIN_TRACING_V2=true`.
See `receipt_agent/tracing/` for the callback handlers and run contexts.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=receipt_agent

# Formatting / linting (repo-wide)
make format
make lint
```

## Places API Caching

The `PlacesCache` in DynamoDB significantly reduces API costs:

| Scenario | Places API Calls | Cost (100 validations) |
|----------|------------------|------------------------|
| Cold cache | 2-3 per validation | ~$3.40 |
| 70% cache hit | 0.6-0.9 per validation | ~$1.02 |
| Warm cache | 0-0.3 per validation | ~$0.30 |

See [docs/ACCESS_PATTERNS.md](docs/ACCESS_PATTERNS.md) for detailed cost analysis.

## Dependencies

- `langgraph>=0.2.0` - Workflow orchestration
- `langchain-openai>=0.2.0` - OpenRouter integration (OpenAI-compatible API)
- `langsmith>=0.1.0` - Tracing and observability
- `pydantic>=2.0.0` - State models
- `receipt_dynamo` - DynamoDB client (local package)
- `receipt_embeddings` - DynamoDB-native vector search (local package)
- `receipt_places` - Google Places client with DynamoDB cache (local package)

## License

MIT
