# Lambda Function Naming Proposal

## Current Issues

1. **Inconsistent suffixes**: Container lambdas get `-lambda-{stack}` suffix, zip lambdas don't
2. **Inconsistent operation naming**: `-vector-compact` vs `-poll`, `-submit`, `-find`
3. **Long names**: `embedding-normalize-poll-batches`, `embedding-mark-batches-complete`
4. **Inconsistent grouping**: Some grouped by type (`line-*`, `word-*`), some by operation (`-poll`, `-submit`)
5. **Realtime suffix**: `-realtime` suffix is inconsistent with other naming

## Current Names

### Zip-based Lambdas (no `-lambda-{stack}` suffix)
- `embedding-list-pending`
- `embedding-line-find`
- `embedding-word-find`
- `embedding-line-submit`
- `embedding-word-submit`
- `embedding-split-chunks`
- `embedding-normalize-poll-batches` (too long)
- `embedding-create-chunk-groups`
- `embedding-prepare-chunk-groups`
- `embedding-prepare-merge-pairs`
- `embedding-mark-batches-complete` (too long)
- `embedding-find-receipts-realtime` (inconsistent suffix)
- `embedding-process-receipt-realtime` (inconsistent suffix)

### Container-based Lambdas (get `-lambda-{stack}` suffix)
- `embedding-line-poll` → `embedding-line-poll-lambda-{stack}`
- `embedding-word-poll` → `embedding-word-poll-lambda-{stack}`
- `embedding-vector-compact` → `embedding-vector-compact-lambda-{stack}` (inconsistent naming)

## Proposed Naming Convention

### Principles
1. **Consistent prefix**: All use `embedding-`
2. **Group by operation type first, then entity type**: `{operation}-{entity}`
3. **Consistent suffixes**: All get `-lambda-{stack}` suffix (or none for zip)
4. **Short, descriptive names**: Max 2-3 words after prefix
5. **Use standard verbs**: `find`, `submit`, `poll`, `compact`, `normalize`, `split`, `prepare`, `mark`

### Proposed Names

#### Submit Workflow (Finding & Submitting)
- `embedding-find-lines` (was `embedding-line-find`)
- `embedding-find-words` (was `embedding-word-find`)
- `embedding-submit-lines` (was `embedding-line-submit`)
- `embedding-submit-words` (was `embedding-word-submit`)

#### Poll Workflow (Polling & Processing)
- `embedding-list-pending` ✓ (keep as-is)
- `embedding-poll-lines` (was `embedding-line-poll`) → `embedding-poll-lines-lambda-{stack}`
- `embedding-poll-words` (was `embedding-word-poll`) → `embedding-poll-words-lambda-{stack}`
- `embedding-normalize-batches` (was `embedding-normalize-poll-batches`)

#### Compaction Workflow (Chunking & Compacting)
- `embedding-split-chunks` ✓ (keep as-is)
- `embedding-create-chunk-groups` ✓ (keep as-is)
- `embedding-prepare-chunk-groups` ✓ (keep as-is)
- `embedding-prepare-merge-pairs` ✓ (keep as-is)
- `embedding-compact` (was `embedding-vector-compact`) → `embedding-compact-lambda-{stack}`
- `embedding-mark-complete` (was `embedding-mark-batches-complete`)

#### Realtime Workflow
- `embedding-find-receipts` (was `embedding-find-receipts-realtime`)
- `embedding-process-receipt` (was `embedding-process-receipt-realtime`)

## Alternative: Group by Entity First

If we prefer grouping by entity type first:

#### Line Embeddings
- `embedding-line-find` → `embedding-line-find`
- `embedding-line-submit` → `embedding-line-submit`
- `embedding-line-poll` → `embedding-line-poll-lambda-{stack}`

#### Word Embeddings
- `embedding-word-find` → `embedding-word-find`
- `embedding-word-submit` → `embedding-word-submit`
- `embedding-word-poll` → `embedding-word-poll-lambda-{stack}`

#### Shared Operations
- `embedding-list-pending` ✓
- `embedding-compact` → `embedding-compact-lambda-{stack}` (or `embedding-compact-lines` and `embedding-compact-words`?)
- `embedding-normalize-batches` ✓
- `embedding-split-chunks` ✓
- etc.

## Recommendation

**Option 1: Operation-First (Recommended)**
- More consistent with REST API naming (`/find`, `/submit`, `/poll`)
- Easier to find all operations of a type
- Better for grouping in AWS console

**Option 2: Entity-First (Current Pattern)**
- More intuitive for line vs word workflows
- Matches current step function organization
- Easier to see all line operations together

## CodeBuild Expectation

Container lambdas MUST use pattern: `{name}-lambda-{stack}` where `name` is the key in `container_configs`.

This means:
- If we rename `embedding-line-poll` → `embedding-poll-lines`, CodeBuild will create `embedding-poll-lines-lambda-{stack}`
- The key in `container_configs` becomes the base name

## Migration Impact

1. Update `lambda_functions.py` config keys
2. Update workflow references in `line_workflow.py` and `word_workflow.py`
3. Update any monitoring/alarms that reference function names
4. Update documentation
5. **Breaking change**: Existing functions in AWS will need to be recreated or aliased

