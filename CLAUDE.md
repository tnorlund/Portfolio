# LayoutLM Training & CoreML Export Pipeline

## Overview

This repository contains the infrastructure for training LayoutLM models on receipt OCR data and exporting them to CoreML format for on-device inference on iOS/macOS.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Training Pipeline                        │
├─────────────────────────────────────────────────────────────────┤
│  DynamoDB (labels)  →  SageMaker (GPU training)  →  S3 (model)  │
└─────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Export Pipeline                          │
├─────────────────────────────────────────────────────────────────┤
│  S3 (checkpoint)  →  SQS Queue  →  Mac Worker  →  CoreML Bundle │
└─────────────────────────────────────────────────────────────────┘
```

## Deployment

Pulumi infrastructure is located in the `infra/` directory:

```bash
cd infra
pulumi up --stack dev
```

After deployment, CodeBuild projects will automatically rebuild container images if source files changed.

## Components

### receipt_layoutlm/
The core training package for LayoutLM token classification on receipts.

**Key files:**
- `trainer.py` - Training orchestration with metrics collection
- `data_loader.py` - DynamoDB data loading and tokenization
- `config.py` - Training and data configuration, including label merge presets
- `export_coreml.py` - PyTorch to CoreML conversion
- `export_worker.py` - SQS-based export job processor (macOS only)
- `cli.py` - Command-line interface

### infra/sagemaker_training/
Pulumi infrastructure for SageMaker-based training.

**Key files:**
- `component.py` - Pulumi component (ECR, CodeBuild, Lambda, IAM)
- `train.py` - Training entrypoint for SageMaker container
- `Dockerfile` - BYOC container definition

## Training

### Starting a Training Job

Via AWS Lambda:
```bash
aws lambda invoke --function-name layoutlm-sagemaker-start-training-<id> \
  --payload '{
    "job_name": "layoutlm-my-experiment",
    "use_spot": false,
    "hyperparameters": {
      "epochs": "10",
      "batch_size": "8",
      "learning_rate": "5e-5",
      "warmup_ratio": "0.1",
      "early_stopping_patience": "2",
      "merge_amounts": "true"
    }
  }' response.json
```

### Hyperparameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `epochs` | 10 | Number of training epochs |
| `batch_size` | 8 | Batch size per GPU |
| `learning_rate` | 5e-5 | Initial learning rate |
| `warmup_ratio` | 0.1 | Warmup steps as fraction of total |
| `early_stopping_patience` | 2 | Epochs without improvement before stopping |
| `merge_amounts` | false | Merge LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL → AMOUNT |

### Label Merge Presets

The `--merge-amounts` flag merges currency-related labels to improve accuracy:
```python
MERGE_PRESETS = {
    "amounts": {"AMOUNT": ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"]},
    "date_time": {"DATE": ["TIME"]},
    "sroie": {  # SROIE-like 4-label setup
        "AMOUNT": [...],
        "DATE": ["TIME"],
        "ADDRESS": ["PHONE_NUMBER", "ADDRESS_LINE"],
    },
}
```

### Monitoring Training

```bash
# Check job status
aws sagemaker describe-training-job --training-job-name <job-name>

# View logs
aws logs get-log-events \
  --log-group-name /aws/sagemaker/TrainingJobs \
  --log-stream-name <job-name>/algo-1-<timestamp>
```

## CoreML Export

CoreML export converts trained PyTorch models to Apple's CoreML format for on-device inference.

### Auto-Export (Default)

**As of #646**, CoreML exports are automatically queued when SageMaker training completes.

```
┌──────────────────┐     ┌────────────────┐     ┌─────────────┐     ┌─────────────┐
│ SageMaker Job    │────▶│ EventBridge    │────▶│ Lambda      │────▶│ SQS Queue   │
│ Completes        │     │ Rule           │     │ queue_export│     │             │
└──────────────────┘     └────────────────┘     └─────────────┘     └─────────────┘
                                                       │                    │
                                                       ▼                    ▼
                                                ┌─────────────┐     ┌─────────────┐
                                                │ DynamoDB    │     │ Mac Worker  │
                                                │ Job lookup  │     │ (polling)   │
                                                └─────────────┘     └─────────────┘
```

**How it works:**
1. EventBridge rule triggers on SageMaker job completion events (job names matching `layoutlm-*`)
2. Lambda handler (`infra/coreml_export/queue_export.py`):
   - Looks up Job entity in DynamoDB via `get_job_by_name()`
   - Gets `best_checkpoint_s3_path` from `job.results`
   - Creates `CoreMLExportJob` record in DynamoDB
   - Sends export message to SQS queue
3. Mac export worker polls queue and processes exports

**Opt-out:** Add tag `skip-coreml-export: true` to the SageMaker training job to skip auto-export.

### Manual Export

#### 1. Queue Export to SQS
```bash
aws sqs send-message \
  --queue-url <coreml-export-job-queue-url> \
  --message-body '{
    "export_id": "<uuid>",
    "job_id": "<training-job-name>",
    "model_s3_uri": "s3://bucket/runs/<job>/checkpoint-<step>/",
    "quantize": "float16",
    "output_s3_prefix": "s3://bucket/coreml/<job>/"
  }'
```

Then run the export worker on macOS:
```bash
# Process one job
layoutlm-cli export-worker --once

# Run continuously
layoutlm-cli export-worker --continuous
```

#### 2. Direct Export (Local)

```bash
layoutlm-cli export-coreml \
  --s3-uri s3://bucket/runs/<job>/checkpoint-<step>/ \
  --output-dir ./output \
  --quantize float16
```

### Export Worker

The export worker must run on macOS (CoreML tools requirement).

**Environment variables:**
- `COREML_EXPORT_JOB_QUEUE_URL` - SQS queue for export jobs
- `COREML_EXPORT_RESULTS_QUEUE_URL` - SQS queue for results
- `DYNAMO_TABLE_NAME` - DynamoDB table for status updates

**SQS Message Format:**
```json
{
  "export_id": "unique-uuid",
  "job_id": "training-job-name",
  "model_s3_uri": "s3://bucket/path/to/checkpoint/",
  "quantize": "float16",
  "output_s3_prefix": "s3://bucket/coreml/output/"
}
```

### Quantization Options

| Mode | Size | Accuracy | Use Case |
|------|------|----------|----------|
| `float16` | ~220MB | Best | Default, good balance |
| `int8` | ~110MB | Good | Size-constrained |
| `int4` | ~55MB | Lower | Experimental |

## Mac OCR + LayoutLM Inference

The Mac worker runs Apple Vision OCR and LayoutLM inference on uploaded receipt images.

### Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Upload    │────▶│   DynamoDB   │     │ SQS Queue   │
│   Lambda    │     │  OCR Job     │     │ (OCR Jobs)  │
└─────────────┘     └──────────────┘     └──────┬──────┘
      │                                         │
      ▼                                         ▼
┌─────────────┐                   ┌─────────────────────────┐
│   S3        │◀──────────────────│   Mac Worker (Swift)    │
│ raw-receipts│                   │  Vision OCR + LayoutLM  │
└─────────────┘                   └─────────────────────────┘
                                         │
        ┌────────────────────────────────┼────────────────────────────────┐
        │                                │                                │
        ▼                                ▼                                ▼
┌─────────────┐             ┌──────────────────┐             ┌────────────────┐
│   S3        │             │   DynamoDB       │             │ SQS Queue      │
│ receipts/   │             │ WordLabels +     │             │ (OCR Results)  │
│ ocr_results/│             │ RoutingDecisions │             └────────────────┘
└─────────────┘             └──────────────────┘
```

### Running the Mac Worker

**One-time Setup:**
```bash
cd receipt_ocr_swift
swift build --configuration release
```

**Process images from dev stack:**
```bash
# The --env flag auto-loads queue URLs and LayoutLM model config from Pulumi outputs
# Model is auto-downloaded from S3 and cached locally

# Process one batch (up to 10 images)
./receipt_ocr_swift/.build/arm64-apple-macosx/release/receipt-ocr --env dev

# Process continuously until queue empty
./receipt_ocr_swift/.build/arm64-apple-macosx/release/receipt-ocr \
  --env dev \
  --continuous \
  --log-level info

# Test mode (no real OCR, useful for testing queue flow)
./receipt_ocr_swift/.build/arm64-apple-macosx/release/receipt-ocr \
  --env dev \
  --stub-ocr \
  --continuous
```

**Process local image (no upload needed):**
```bash
./receipt_ocr_swift/.build/arm64-apple-macosx/release/receipt-ocr \
  --process-local-image ~/test-receipt.png \
  --output-dir ~/output \
  --layoutlm-model ~/.models/layoutlm \
  --log-level debug
```

**CLI Options:**
| Flag | Description |
|------|-------------|
| `--env <env>` | Load config from Pulumi stack (dev/prod) |
| `--continuous` | Process until queue empty |
| `--log-level` | trace, debug, info, warn, error |
| `--layoutlm-model` | Path to local CoreML model bundle |
| `--layoutlm-cache-path` | Where to cache downloaded model (default: `.models/layoutlm`) |
| `--stub-ocr` | Skip real OCR (testing) |

### LayoutLM Model Location

The worker downloads the CoreML model from S3 if configured:
- **S3 path**: `s3://<bucket>/coreml/LayoutLM.mlpackage/`
- **Local cache**: `~/.models/layoutlm/` (or `--layoutlm-cache-path`)

Model bundle contents:
```
model-bundle/
├── LayoutLM.mlpackage/   # CoreML compiled model
├── vocab.txt             # BERT tokenizer vocabulary
├── config.json           # Label definitions
└── label_map.json        # Label ID → name mapping
```

### What the Worker Does

For each image in the queue:
1. Downloads image from S3
2. Runs Apple Vision OCR (text + bounding boxes)
3. Runs LayoutLM inference (token classification)
4. Uploads results to S3 (`receipts/`, `ocr_results/`)
5. Creates `ReceiptWordLabel` records in DynamoDB
6. Sends completion message to results queue

### Key Files

| Component | Path |
|-----------|------|
| Swift CLI entry | `receipt_ocr_swift/Sources/ReceiptOCRCLI/main.swift` |
| Config (Pulumi loader) | `receipt_ocr_swift/Sources/ReceiptOCRCore/Config/Config.swift` |
| OCR Worker | `receipt_ocr_swift/Sources/ReceiptOCRCore/Worker/OCRWorker.swift` |
| Vision OCR | `receipt_ocr_swift/Sources/ReceiptOCRCore/OCR/VisionOCREngine.swift` |
| LayoutLM inference | `receipt_ocr_swift/Sources/ReceiptOCRCore/LayoutLM/LayoutLMInference.swift` |
| Model downloader | `receipt_ocr_swift/Sources/ReceiptOCRCore/AWS/ModelDownloader.swift` |

## Swift Integration

The exported CoreML model is used in the iOS/macOS app via the `receipt_ocr_swift` package.

### Timestamp Compatibility

Python's `datetime.fromisoformat()` doesn't accept `Z` suffix. Swift code must use:
```swift
// Correct - Python compatible
df.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSxxx"  // Produces +00:00

// Incorrect - Python will fail
df.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSXXXXX"  // Produces Z
```

## Typical Workflow

1. **Train model** on SageMaker with labeled receipt data
2. **Monitor training** via CloudWatch logs
3. **Auto-export queued** - EventBridge + Lambda automatically queues CoreML export (or manually queue)
4. **Run export worker** on Mac to convert to CoreML:
   ```bash
   ~/.coreml-venv/bin/layoutlm-cli export-worker --once \
     --job-queue-url "https://sqs.us-east-1.amazonaws.com/<account>/coreml-export-prod-prod-coreml-export-job-queue" \
     --results-queue-url "https://sqs.us-east-1.amazonaws.com/<account>/coreml-export-prod-prod-coreml-export-results-queue"
   ```
5. **CoreML bundle uploaded** to S3 automatically by export worker
6. **Update iOS app** to use new model

### Export Worker Python Environment

CoreML tools has version constraints that don't work with bleeding-edge Python:
- **Python 3.14**: Not supported (typing compatibility issues)
- **Torch 2.9+**: Not tested with coremltools 9.0
- **scikit-learn 1.6+**: Not supported (max 1.5.1)

Create a compatible venv using Python 3.12:
```bash
# Create venv with compatible Python version
/usr/local/bin/python3.12 -m venv ~/.coreml-venv

# Install dependencies
~/.coreml-venv/bin/pip install coremltools 'torch<2.8' transformers boto3

# Install local packages (editable mode for development)
~/.coreml-venv/bin/pip install -e receipt_layoutlm -e receipt_dynamo

# Run export worker
~/.coreml-venv/bin/layoutlm-cli export-worker --once \
  --job-queue-url "<queue-url>" \
  --results-queue-url "<results-queue-url>"
```

**Note:** Even with Python 3.12, you may see warnings about version compatibility. These are typically non-fatal.

## QA Agent Evaluation Workflow

The QA agent answers 32 marquee questions about receipt data. We iterate on answer quality using a deploy → run → evaluate → grade loop.

### Architecture

```text
Code Change → Pulumi Deploy → Step Function (32 Qs) → S3/LangSmith
                                                           ↓
                                              Dev API (/qa/visualization)
                                                           ↓
                                              Claude Code agents (8x parallel)
                                                           ↓
                                              q00.md-q31.md + SCORECARD.md
```

### Key Files

| Component | Path |
|-----------|------|
| QA graph (plan/agent/tools/shape/synthesize) | `receipt_agent/receipt_agent/agents/question_answering/graph.py` |
| QA state schema | `receipt_agent/receipt_agent/agents/question_answering/state.py` |
| QA tools (search, get_receipt, etc.) | `receipt_agent/receipt_agent/agents/question_answering/tools/search.py` |
| Agent system prompt | `receipt_agent/receipt_agent/agents/question_answering/tools/__init__.py` |
| Step function infra | `infra/qa_agent_step_functions/infrastructure.py` |
| Run-question Lambda | `infra/qa_agent_step_functions/lambdas/run_question.py` |
| Viz cache API handler | `infra/routes/qa_viz_cache/lambdas/index.py` |
| Scorecard | `SCORECARD.md` |
| Per-question evals | `qa-eval-q{NN}/qa_evaluation/q{NN}.md` (git worktrees) |

### Step-by-Step Process

#### 1. Make code changes

Edit files in `receipt_agent/receipt_agent/agents/question_answering/`. The main files are `graph.py` (workflow nodes and prompts), `state.py` (state schema), and `tools/search.py` (tool implementations and state_holder population).

#### 2. Deploy to dev

```bash
cd infra && pulumi up --stack dev --yes
```

This rebuilds container images via CodeBuild if source files changed (~5 min). The QA agent Lambda image includes `receipt_agent`, `receipt_dynamo`, and `receipt_chroma`.

#### 3. Run the step function

```bash
aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:us-east-1:<account-id>:stateMachine:qa-agent-dev" \
  --input '{"langsmith_project": "qa-eval-<descriptive-name>"}' \
  --region us-east-1
```

The step function runs all 32 questions, queries receipt metadata, exports LangSmith traces, and runs an EMR Spark job to build the viz cache. Takes ~20-25 min total.

Monitor with:
```bash
aws stepfunctions describe-execution \
  --execution-arn "<execution-arn>" --region us-east-1 \
  --query '{status: status, startDate: startDate, stopDate: stopDate}'
```

#### 4. Verify the viz cache is ready

```bash
curl -s "https://dev-api.tylernorlund.com/qa/visualization" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(json.dumps(d.get('metadata', {}), indent=2))
"
```

Check that `cached_questions` is 32 and the `execution_id` matches your run.

#### 5. Evaluate all 32 questions with parallel agents

Spawn a team with 8 agents, each handling 4 consecutive questions (Q0-Q3, Q4-Q7, ..., Q28-Q31). Each agent:

1. Fetches the question result: `curl -s "https://dev-api.tylernorlund.com/qa/visualization?index=N"`
2. Parses the `trace` array to extract plan, tool calls, agent reasoning, shape info, and final answer
3. Reads the existing evaluation from the git worktree: `/path/to/qa-eval-q{NN}/qa_evaluation/q{NN}.md`
4. Verifies answer correctness using MCP receipt tools (`search_receipts`, `search_product_lines`, `get_receipt_summaries`, `get_receipt`, etc.)
5. Writes the updated evaluation with grade, correct answer, issues, and comparison to previous run
6. Reports grades back to the team lead

Use `subagent_type: general-purpose` with `mode: bypassPermissions` and `run_in_background: true`. Each agent needs access to bash (for curl), file read/write (for q*.md files), and MCP receipt tools (for verification).

#### 6. Update the scorecard

After all agents complete, update `SCORECARD.md` with:
- New column in the Scores table
- New row in the Run Log with commit hash, date, LangSmith project
- New row in the Summary table with grade distribution counts
- New step in the Iteration History section explaining what changed and why

#### 7. Commit and push

Commit `SCORECARD.md` to the evaluation branch. The per-question `q*.md` files live in separate git worktrees on their own branches (`chore/qa-eval-q{NN}`).

### Dev API Reference

| Endpoint | Returns |
|----------|---------|
| `GET /qa/visualization` | Metadata only (question count, costs, execution ID) |
| `GET /qa/visualization?index=N` | Single question with full trace |
| `GET /qa/visualization?all=true` | All 32 questions |

Response `trace` array items have `type` field: `plan`, `agent`, `tools`, `shape`, `synthesize`.

### Evaluation Format

Each `q{NN}.md` follows this structure:

```markdown
# Q{N}: {question text}

## Metadata
| Field | Value |
| Question Index / Trace ID / Cost / LLM Calls / Tool Invocations / Receipts |

## Plan
## Tool Calls
## Agent Reasoning
## Shape
## Final Answer
### Evidence

## Evaluation
**Grade:** {A-F with +/-}
**Correct Answer:** {MCP-verified ground truth}
**Tool Efficiency:** {N} calls ({assessment})
**Issues:** {numbered list}
**Improvements from Baseline:** {comparison to previous run}
```

### Grading Scale

- **A**: Correct answer with proper evidence, matches MCP verification
- **B**: Mostly correct, minor issues (slightly off amounts, missing some evidence)
- **C**: Partially correct or missing important context
- **D**: Significant errors in answer or evidence
- **F**: Wrong answer or critical data loss

When grading, pay special attention to whether the synthesizer agrees with the agent's reasoning. If the agent is correct but the synthesizer contradicts it, that's a D or F (synthesis override bug). If they agree, focus on whether the agent's own analysis is correct.

## Related Issues

- #645: Auto-queue CoreML export after training completion (implemented in #646)
- #647: Two-pass LayoutLM for hierarchical classification
- #567: SageMaker training infrastructure

## Packages

| Package | Description |
|---------|-------------|
| `receipt_layoutlm` | LayoutLM training, inference, and export |
| `receipt_dynamo` | DynamoDB entities and data layer |
| `receipt_chroma` | ChromaDB vector store for label validation |
| `receipt_upload` | Receipt processing and label validation |
| `receipt_agent` | LangGraph agents for receipt analysis |
