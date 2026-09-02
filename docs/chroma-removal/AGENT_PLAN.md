# Agent build-out plan: Chroma removal

Companion to [SPEC.md](SPEC.md). Decomposes the implementation into agent-sized
tasks, each with a contract, a local eval command, and numeric acceptance
criteria. Stacked-PR topology; parallel where the DAG allows.

## Principles

1. **Harness before implementations.** PR A ships the evaluation harness and
   golden fixtures captured from live Chroma. Every later agent codes against
   its interface and is graded by its scorecard. Capture is time-sensitive:
   once Chroma is torn down, the reference answers are unobtainable.
2. **Agents are done when the eval says so.** Each task card names the command
   and thresholds. No self-graded completion.
3. **Stack rules** (established in this repo): parents merge with `--merge`,
   leaf/child PRs squash; never squash a stack parent. Codex/`/code-review`
   pass per layer before it merges.
4. One agent per worktree; branch names `chroma-rm/<task-id>`.

## Test tiers

| Tier | Where | Mechanism |
|---|---|---|
| Unit | fully local, CI | `VectorSearchClient` interface + `FakeVectorIndex` (numpy exact cosine over fixture vectors; deterministic). SearchVectors has no local emulator and moto cannot mock it. |
| Parity | fully local, offline | golden fixtures (captured from live Chroma) + `evaluate.py` scorecard — no AWS needed |
| Integration | local machine → dev table | real SearchVectors on `ReceiptsTable-dc5be22`; pytest marker `vector_integration`, skipped without creds |

## The harness (PR A — build first)

```
scripts/similarity_harness/
  capture_golden.py    # run NOW against live Chroma Cloud (dev):
                       #  - merchant resolution: neighbors + tier + decision per golden receipt
                       #  - word top-30 neighbors (ids + distances) for sampled words
                       #  - section-verifier votes per golden receipt
                       #  → JSON fixtures in tests/fixtures/similarity/ (committed)
  evaluate.py          # --backend {fake,dynamo,chroma} → scorecard.json:
                       #  neighbor recall@k vs fixtures, merchant agreement %,
                       #  tier-decision agreement, p50/p95 latency, est. $/query
receipt_embeddings/vector_client.py   # VectorSearchClient protocol:
                                      #  search(vector, index, top_k, filters) -> [ScoredItem]
                                      #  get_vector(key) -> vector
receipt_embeddings/testing/fake_index.py  # FakeVectorIndex (exact NN)
```

Golden set: reuse the line-item golden receipts + the 43-image May-26 batch
(known merchant ground truth). Fixture capture uses Chroma Cloud dev.

## Task cards

### Stack 1 — the build (serial spine, parallel leaves)

| ID | Task | Base | Eval / acceptance |
|---|---|---|---|
| **A** | Harness + golden fixtures + `VectorSearchClient` + `FakeVectorIndex` | main | `capture_golden.py` produces fixtures for ≥40 receipts; `evaluate.py --backend chroma` scores ≈1.0 self-parity (sanity); unit tests for fake index |
| **B** | `receipt_embeddings` package: relocate `formatting` + `openai` out of receipt_chroma; Swift parity fixtures regenerated same PR | A | swift-ci green (byte-diff parity gate); no `chromadb` import anywhere in package; existing formatting tests pass relocated |
| **C** | Embedding-item entities + embed-and-put writer + backfill script | B | unit vs fake; integration: backfill 50 dev receipts → items exist (GetItem), attrs per §3.3; idempotent re-run writes nothing new |
| **D** | Index bootstrap (Pulumi or scripted UpdateTable) + stream-processor freshening leg + `*_EMBEDDING` skip guard | C | dev indexes ACTIVE; `evaluate.py --backend dynamo` neighbor recall@10 ≥ 0.9 vs fixtures; label/place edit on dev propagates to embedding item < 60s |
| **E1** | Port merchant resolution behind `VECTOR_BACKEND` | D | merchant agreement ≥ 98% on golden set; tier distribution within ±5%; Places-call count not higher |
| **E2** | Port section verifier + PRODUCT_NAME proposer | D | agree/disagree/abstain votes match fixtures ≥ 95% |
| **E3** | Port QA search + MCP semantic modes + new `similar_labeled_words` (BOTH MCP copies: script + vendored Lambda) | D | QA marquee scorecard (local_qa_run.py) no worse than baseline; `similar_labeled_words` returns nonzero evidence on the 1,074 GRAND_TOTAL words sample |
| **E4** | /receipt figure generators → DynamoDB + §5a copy/types rewrite | D | generators produce valid cache JSON on dev; e2e fixtures updated; no "chroma" string in rendered page |

E1–E4 are **parallel agents** — same base (D), disjoint files.

### Stack 2 — deletions (independent of Stack 1, start immediately, parallel)

| ID | Task | Eval / acceptance |
|---|---|---|
| **X1** | Delete 6 dead label-query paths + label_refresh component + tools_simplified | CI green; `evaluate.py --backend chroma` unchanged (proves paths were dead) |
| **X2** | Delete dormant embedding_step_functions tree (drop legacy-URN aliases same change) + combine_receipts + pattern_builder | `pulumi preview` clean on both stacks; CI green |
| **X3** | Port ~11 disguised exact-lookups to DynamoDB queries | each call site: same results on 10 sampled inputs (before/after script in PR body); dummy-embedding OpenAI call gone |
| **X4** | Dead-code sweep (§6 G list) | CI green; grep proof in PR body |

### Cutover + teardown (human-gated, not agent tasks)

Flag flips, prod backfill, Phase 4 ordered teardown, VPC deletion (after EIP
allowlist check), Chroma Cloud account closure — driven by you with the spec's
§5 checklist; agents prepare the PRs, you sequence the merges and deploys.

### Stack 3 — post-teardown packaging (after Phase 4)

| ID | Task | Base | Eval / acceptance |
|---|---|---|---|
| **Z1** | Zip-package post-Chroma Lambdas (not LayoutLM / SageMaker) | Phase 4 complete; no `receipt_chroma` in the image | `python scripts/lambda_zip_budget.py` shows zero `chroma_blocked` rows for the converted functions; unzipped size on AL2023 arm64 **< 200 MB**; `pulumi preview` on `tnorlund/portfolio/dev` shows only the intended `PackageType` replacements; LayoutLM Dockerfile still installs CPU PyTorch |

Do **not** start Z1 while Dockerfiles still `pip install receipt_chroma`.
Classifier: [ZIP_LAMBDA_FOLLOWUP.md](ZIP_LAMBDA_FOLLOWUP.md).

## Comparing implementations

One scorecard, run identically per candidate branch:

```
python scripts/similarity_harness/evaluate.py --backend dynamo --out scorecard.json
```

| Metric | Source |
|---|---|
| neighbor recall@k vs golden Chroma answers | parity fixtures |
| merchant agreement %, tier decisions | fixtures |
| p50/p95 SearchVectors latency | integration run |
| est. cost/query | request units from responses |
| diff size / new deps | git |

Candidates are diffed as scorecards, not as prose claims. If two agents
compete on one task (worth it only for E1), same fixtures, same command,
pick the better scorecard.

## Review cadence

- Each PR: codex or `/code-review` at its layer before merge; you review the
  scorecard delta alongside the diff.
- Stack 2 merges freely (independent). Stack 1 merges bottom-up: A → B → C →
  D, then E1–E4 in any order.
