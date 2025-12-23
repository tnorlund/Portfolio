# LangSmith State Injection in AWS Step Functions

## Question 1

I have a langchain evaluator agent that I'd like to test at scale in an AWS step function. Since many runs require the same data, I'd like to download everything separately and inject the state and everything else into the agent so that I can still see each step in langsmith, but get the efficiency of querying the data once. How can I do this?

---

## Response 1

You want two things that seem at odds, but you can absolutely get both:

1. **Only fetch "shared" data once**, and
2. **Still emit full per-run LangSmith traces** (every tool/LLM call inside each evaluator run).

The trick is: **don't pass the big shared data through Step Functions state** (you'll hit the 256 KB payload cap) — pass a **reference** (e.g., `shared_ctx_id` / S3 URI), and make your agent **load it from a warm cache** (or local disk) while still tracing normally.

### Recommended architecture

#### A) "Prefetch once" step (single task)

**Step 1: Build the shared context**

- Fetch/query everything that's reused across runs (merchant metadata, embeddings index snapshot, label maps, etc.).
- Serialize it into a compact artifact (JSONL / parquet / msgpack / sqlite).
- Store it in **S3** (and optionally write a small "manifest" record to DynamoDB).
- Return only:
  - `shared_ctx_id`
  - `s3_uri` (or key)
  - `version` / `etag` / checksum

Why: Step Functions payload is capped at **256 KB** per state input/output → don't inline shared data.

#### B) Parallel evaluator runs (Map / Distributed Map)

**Step 2: Run your evaluator at scale**

- Use a **Map** state for moderate scale, or **Distributed Map** if you're doing large datasets / high concurrency / long histories.
- Each iteration receives:
  - `example_id` (or the input record)
  - `shared_ctx_id` (or `s3_uri`)
  - tracing metadata (batch id, git sha, etc.)

#### C) Inside each worker: load shared data via a cache, then run agent normally

In the Lambda/ECS task that runs the evaluator:

1. **Resolve shared context**
   - Check local cache first:
     - Lambda: `/tmp/shared/{shared_ctx_id}` (warm across invocations on the same container)
     - ECS/Fargate: ephemeral disk, or EFS if you truly want cross-task sharing
   - If missing: download from S3 once, store locally, reuse.

2. **Invoke your LangChain evaluator with LangSmith tracing enabled**
   - Pass **tags + metadata** in `RunnableConfig` so each run is searchable/correlatable in LangSmith. LangSmith explicitly supports tags/metadata via `RunnableConfig`.

This gives you "query once" efficiency *and* full traces because each run still executes the chain/tools/LLM calls (they just read from local cached context instead of re-querying upstream sources).

---

### How to "inject state" into the agent without bloating payloads

#### Pattern 1 (most common): pass a `shared_ctx_id`, load internally

- Step Functions passes `shared_ctx_id` (tiny).
- Your agent/tooling layer uses `shared_ctx_id` to load the real object from cache.

**Where to put this in LangChain?**

- Put `shared_ctx_id` into the run **config** (`metadata` and/or `configurable`) so it's visible in LangSmith.
- Use a small wrapper runnable/tool/retriever that does: `ctx = get_shared_context(shared_ctx_id)`.

LangChain supports sending metadata/tags through `RunnableConfig`, which LangSmith records.

#### Pattern 2: configurable fields (LCEL) for "context reference"

If you're using LCEL / runnables, you can define a configurable field and provide it at invoke-time (still small, like an ID). LangChain exposes configurable fields for runnables.

---

### Step Functions details that matter for this design

- **Payload limit:** 256 KB per state input/output → don't inline shared data.
- **Execution history limit (Standard):** 25,000 events → large fan-out can hit this; Distributed Map helps by making each iteration a child execution.

---

### What I'd implement (concrete checklist)

1. **Prefetch Lambda**
   - Build `shared_ctx_id = hash(query_params + data_version)`
   - Write artifact to `s3://…/shared/{shared_ctx_id}.bin`
   - Return `{shared_ctx_id, s3_key, version}`

2. **Distributed Map**
   - Item source: your list of examples (S3 JSONL is common)
   - Each item → evaluator worker with `{example, shared_ctx_id}`

3. **Evaluator worker (Lambda or ECS)**
   - `ctx = load_shared_context(shared_ctx_id)` with local disk cache
   - Call `evaluator.invoke(example, config=...)` and include:
     - `tags`: `["scale_eval", "batch:2025-12-22", f"ctx:{shared_ctx_id}"]`
     - `metadata`: `{ "shared_ctx_id": ..., "example_id": ..., "git_sha": ... }`
   - Result saved somewhere (S3/DDB) + returned (small)

4. **LangSmith correlation**
   - Use tags/metadata to filter runs for a specific batch/context in the UI.

---

### Two practical tips to keep it fast *and* traceable

- **Cache at the "retriever/tool boundary."** Don't try to inject a massive Python object into the agent prompt; instead, make the *things that used to query data* read from the cached context.
- **Keep the shared context immutable + versioned.** If `shared_ctx_id` implies a content hash/version, you can safely reuse caches and compare results across runs.

---

If you tell me what your "shared data" is (S3 files? DynamoDB query results? Chroma/Pinecone snapshots? merchant metadata?) and whether you're running the worker on **Lambda vs ECS/Fargate**, I can sketch a concrete Step Functions state machine (Prefetch → Distributed Map) and a reference `load_shared_context()` caching implementation that fits your setup.

---

## Question 2

You misunderstood me. The state I'm trying to inject into is the langsmith state. I want to see the initial LLM query of getting similar documents, and finding patterns, and then the evaluator LLM calls, all in the respective trace in langsmith, but I'm programatically splitting this up in the step function for efficiency. Can I do this?

---

## Response 2

Yes — you *can* split the work across Step Functions **and still have it appear as one unified LangSmith trace**, with the "similar docs / patterns" step showing up *before* the evaluator calls, **as long as you propagate LangSmith trace context between steps**.

LangSmith supports this explicitly as **distributed tracing** via context propagation headers (`langsmith-trace` + optional `baggage`).

The key mental model:

- **LangSmith trace state ≈ a RunTree context** (trace "dotted order" + project/tags/metadata).
- You can **export** that context (as headers) at the end of Step A,
- pass it through Step Functions (just small strings),
- then **resume** it in Step B/C so their runs become **children in the same trace**.

---

### The pattern that works in Step Functions

#### Step A (prefetch / retrieval / pattern finding)

1. Start a traced block (root run).
2. Do your "get similar docs + find patterns" LLM/tool calls inside it.
3. At the end, **return `run_tree.to_headers()`** (or the `langsmith-trace` / `baggage` values) in the Step Function output.

LangSmith docs show `run_tree.to_headers()` as the propagation mechanism.

#### Step B (the evaluator runs — Map / distributed map)

For each item (each evaluation):

1. Accept the propagated headers from Step A.
2. Create a **child run context** (so each item is a distinct child).
3. Run your evaluator chain/agent normally with tracing enabled.

You can "receive" the trace via `ls.tracing_context(parent=...)` (docs show passing headers directly as `parent`).

---

### Concrete Python sketch (what you're trying to do)

#### A) Prefetch task (produces trace context)

```python
import langsmith as ls
from langsmith.run_helpers import get_current_run_tree

@ls.traceable(name="prefetch_similar_docs_and_patterns")
def prefetch(inputs: dict) -> dict:
    # ... your retrieval + pattern-finding LLM/tool calls here ...
    # (still traced because we're inside @traceable)

    rt = get_current_run_tree()
    # Export trace context for downstream steps
    headers = rt.to_headers() if rt else {}
    return {
        "shared_ctx_s3_uri": "...",
        "langsmith_parent_headers": headers,
    }
```

The "export headers from the current run tree" flow is exactly what LangSmith describes for distributed tracing.

#### B) Evaluator task (consumes trace context; runs as a child in the same trace)

```python
import langsmith as ls

def evaluate_one(example: dict, parent_headers: dict) -> dict:
    # Resume the trace *as a child* of the parent step
    with ls.tracing_context(parent=parent_headers):
        # Everything inside here becomes part of the same unified trace
        # ... run your evaluator agent/chain ...
        result = {"score": 1.0}
        return result
```

LangSmith docs show this exact "server receives headers → `tracing_context(parent=...)`" mechanism.

---

### Important nuance for Map fan-out

If you pass the **same** `langsmith_parent_headers` into *every* Map iteration and just call `tracing_context(parent=...)`, you'll usually get what you want: each evaluator run appears in the same trace as a child.

If you want cleaner structure (recommended): in Step B, first create a **per-item child RunTree** and pass *that* child's headers into the actual evaluator call (especially if you're calling another service). The RunTree API supports `create_child()` and converting to/from headers.

---

### When you *can't* make it a single trace

You won't be able to "merge" two already-finished, independent traces after the fact. You must propagate context *at runtime* (or manually instrument with RunTree and parent/child relationships). LangSmith's manual instrumentation docs explicitly call out RunTree for assembling traces, but warn it's easy to get wrong if you don't propagate context correctly.

---

### Practical checklist for Step Functions

- ✅ **Return only trace context** between steps (headers), not big data.
- ✅ In each downstream Lambda/ECS task, call `ls.tracing_context(parent=...)` before invoking the evaluator chain.
- ✅ Add `tags/metadata` per Map item so you can filter runs in LangSmith (example_id, batch_id, etc.). (The propagated `baggage` can also carry tags/metadata.)

---

If you tell me which runtime you're using for each step (**Lambda vs ECS/Fargate**) and whether the evaluator step is **parallel Map**, I'll give you an exact wiring for:

- what keys to store in state (`langsmith-trace` / `baggage`),
- how to structure the trace so each Map item becomes a clean child ("Eval #123", "Eval #124", …),
- and how to preserve the "retrieval/pattern" LLM calls as the first span in the same trace.
