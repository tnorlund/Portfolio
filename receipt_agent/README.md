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
- ☁️ **OpenRouter**: Uses OpenRouter for LLM reasoning
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
# LLM Configuration (OpenRouter)
export OPENROUTER_API_KEY="your-openrouter-key"
export OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
export OPENROUTER_MODEL="openai/gpt-5.5"
# Optional cost controls for local synthesis/audits:
export RECEIPT_AGENT_DISABLE_PAID_LLM="1"        # no paid LLM client creation
export OPENROUTER_MODEL_PROFILE="cheap"         # quality|balanced|cheap|nano

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

## Synthetic Receipt Augmentation

Use the offline synthetic replay tools when you want to synthesize high-fidelity
train-only LayoutLM examples from local receipt JSON without paying for LLM,
AWS, or Places calls.
The workflow is merchant-generic: include all exported merchants in the input
directory, then use Sprouts or any other dense merchant as a focused validation
case when inspecting quality. Already grouped merchant exports work directly;
ungrouped exports are normalized by attaching matching top-level `lines`,
`words`, and `word_labels`, normalizing bounding boxes, ignoring invalid labels,
and inferring merchants from `MERCHANT_NAME` labels before falling back to
header text.

### No-Spend Local Workflow

Run these commands from the Portfolio repo root:

```bash
# Prevent paid LLM client creation while building/auditing local artifacts.
export RECEIPT_AGENT_DISABLE_PAID_LLM="1"
export DISABLE_PAID_LLM="1"

# Build merchant pattern artifacts, create a LayoutLM-ready bundle, and embed
# a bounded synthesis_quality_report in the bundle JSON.
python3.12 scripts/verify_synthetic_replay.py local-pipeline \
  --receipt-dir ./local_receipt_json \
  --artifact-output-dir ./.tmp/synthetic-artifacts \
  --bundle-output ./.tmp/synthetic-bundle.json \
  --min-ready-share 0.0 \
  --min-avg-readiness-score 0.0 \
  --min-grounded-candidate-share 0.0

# Re-read the embedded report without rebuilding artifacts.
python3.12 scripts/verify_synthetic_replay.py report \
  --bundle-file ./.tmp/synthetic-bundle.json
```

The local pipeline groups receipts by merchant, discovers receipt structure,
generates merchant-local candidates, applies LayoutLM quality gates, enforces
merchant synthesis contracts, and writes a bundle with:

- `synthetic_training_examples`: train-only LayoutLM rows (`tokens`, `bboxes`,
  `ner_tags`) accepted by the same loader used during training.
- `selection.accepted_candidate_examples`: ranked accepted examples with
  merchant, operation, structure similarity, candidate quality, preview lines,
  and selection reason for no-spend review.
- `source_receipt_quality`: per-merchant input coverage for receipts, lines,
  words, usable labels, line-item labels, totals, blockers, and limitations.
- `merchant_synthesis_contracts`: per-merchant safe operations, mutable fields,
  category/catalog evidence, quality gates, blockers, and limitations.
- `candidate_mix`: accepted/rejected counts by merchant, operation, category,
  field replacement, structure similarity, and accepted mix balance.
- `candidate_quality`: deterministic no-spend quality evidence derived from
  LayoutLM gate inputs (structure similarity, component pass rate, operation
  evidence, arithmetic, layout, and real-baseline checks when available).
  Derived `high_fidelity` additionally requires independent scored layout
  evidence or a robust in-range real-baseline comparison.
- `synthetic_training_batch_policy`: a no-spend stoplight for the next
  train-only run. `hold` means do not train the synthetic rows yet,
  `smoke_test_only` means try only the tiny accepted batch with real validation,
  and `bounded_augmentation` means the selected batch has high-fidelity
  candidate-quality evidence and passed the overtraining concentration checks.
- `synthesis_quality_report`: bounded reviewer-facing evidence with readiness,
  recommendations, merchant acceptance rates, preview lines, arithmetic checks,
  and grounding checks.

### Training Handoff

Point LayoutLM training at the generated bundle. Synthetic rows are loaded only
into the training split; validation remains real receipts only. The loader also
enforces embedded bundle holds: a bundle whose `synthesis_quality_report`
declares `training_ready: false` is rejected before any synthetic rows are mixed
into training.

```bash
export LAYOUTLM_SYNTHETIC_TRAINING_EXAMPLES="./.tmp/synthetic-bundle.json"

# Or pass the equivalent CLI flag when launching training:
python3.12 -m receipt_layoutlm.cli train \
  --synthetic-training-examples ./.tmp/synthetic-bundle.json
```

The loader rejects candidates that are not `train_only`, have invalid sequence
shape or bounding boxes, fall below the structure-similarity threshold, lack
cross-receipt grounding for added items, fail non-taxable arithmetic
reconciliation, mutate unsupported fields, or are not allowed by the merchant
contract. Per-merchant and per-operation caps prevent one merchant or mutation
type from dominating augmentation. The bundle and training metrics also expose
`accepted_mix_balance`, which reports top-merchant/top-operation share,
normalized entropy, and concentration risk for overtraining review. The
synthetic augmentation audit treats medium/high mix-balance risk as a promotion
hold even when targeted F1/confusion metrics improve.
If an artifact includes blocked `source_receipt_quality`, the merchant contract
is marked blocked and all of that merchant's synthetic candidates are rejected
before training. Limited source quality can also disable specific operations,
such as line-item arithmetic when labeled line items or grand-total labels are
missing. The synthesis quality report and training-metrics API expose the same
source-quality status, label counts, and per-operation blockers for review.
Unlabeled OCR exports can still report diagnostic text-structure evidence such
as price-like lines, total-like anchors, and line-item-like text. Those fields
help prioritize labeling work, but they do not unblock training until validated
word labels are present.
Preflight summaries keep raw `candidate_count` separate from `accepted_count`:
pattern artifacts may contain candidate sketches, but `accepted_count` remains
zero until bundle selection and the training-batch quality policy accept them.
Candidate quality also includes a nearest-real structure gate. Inspect
`structure_similarity.nearest_real_receipt_key`, `shape_deltas`, and
`match_summary.shape_checks` to see which real receipt the candidate resembles
and how closely its line count, item count, token count, price column, spacing,
and category pattern match. Inspect `real_baseline_comparison` to see whether
the synthetic score falls inside the merchant's normal real-to-real structure
range. `high_fidelity` requires the same structure-similarity and component
thresholds used by the LayoutLM loader.

### What Can Be Safely Mutated

The current deterministic path supports:

- Merchant-local hard negatives for known false positives.
- Adding observed, non-taxable line items seen on other receipts from the same
  merchant, with category placement and total reconciliation.
- Removing removable non-taxable line items, with summary totals adjusted.
- Replacing stable DATE/TIME fields only when format, geometry, and multiple
  observed values agree.
- Recording taxable item and tax-rate evidence in the merchant contract while
  keeping tax-changing mutations blocked until the loader has explicit gates for
  taxable arithmetic.

For Sprouts receipts, the synthesizer uses the Sprouts-specific geometry and
section patterns first. Other merchants use the generic merchant synthesis
profile until a merchant-specific parameterizer is added.
Do not promote a merchant-specific parameterizer unless the generic contract
path already shows the repeatable structure it is specializing.

### LLM Model Controls

The default OpenRouter model follows the current OpenAI latest-model guidance:
`openai/gpt-5.5`. The source is the official OpenAI latest model page:
https://developers.openai.com/api/docs/guides/latest-model
This was last verified against official OpenAI developer docs on 2026-06-23.

For cost-controlled runs, prefer profiles unless a specific model is required:

```bash
export OPENROUTER_MODEL_PROFILE="cheap"   # quality|balanced|cheap|nano
unset OPENROUTER_MODEL                    # explicit model overrides profile
```

For fully local synthesis and tests, keep `RECEIPT_AGENT_DISABLE_PAID_LLM=1`.
Commands that start deployed replay or wait on deployed audits (`start`, `audit`,
and deployment `status`) can touch AWS resources; use them only when you intend
to run deployed infrastructure.

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
- `langchain-openai>=0.2.0` - OpenRouter integration (OpenAI-compatible API)
- `langsmith>=0.1.0` - Tracing and observability
- `chromadb>=0.5.0` - Vector similarity search
- `pydantic>=2.0.0` - State models
- `receipt_dynamo` - DynamoDB client (local package)
- `receipt_chroma` - ChromaDB client (local package)

## License

MIT
