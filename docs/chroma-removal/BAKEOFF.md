# Tournament: four-tool layer-by-layer build-out

Every layer of Stack 1 (and optionally the X-cards) is implemented by all four
tools — Claude, Codex, Grok Build, Cursor — reviewed together, and the winner
merges before the next round begins on top of it. Identical packet per round:
this file + [SPEC.md](SPEC.md) + [AGENT_PLAN.md](AGENT_PLAN.md) task card.

Standing rules (all rounds):
- Branch `bakeoff/<round>/<tool>`, draft PR against the current winner branch.
- Do not modify prior winners' code except where the card says to.
- Never create/alter vector indexes (5-index budget; spec-fixed; judge-scripted).
- Never touch prod. Dev-table writes only where the card allows; the judge
  wipes between candidates.
- Done = the round's gates pass, not "looks complete."

Judge duties (non-competitive, regardless of who wins rounds): dev index
creation script, dev-table wipes between phase-2 runs, assembling the
side-by-side comparison report for review.

## Round A — harness, fixtures, interface

Build: `scripts/similarity_harness/{capture_golden.py,evaluate.py}`,
`receipt_embeddings/vector_client.py` (VectorSearchClient protocol),
`receipt_embeddings/testing/fake_index.py` (exact-NN fake). See AGENT_PLAN
"The harness".

**Rubric (pre-committed — the harness grades later rounds, so it is judged
against this, not self-graded):**
1. Fixture coverage: ≥40 golden receipts; all three query families captured
   (merchant-resolution neighbors+tier+decision; word top-30 neighbors with
   distances; section-verifier votes).
2. Metrics match SPEC §8 definitions (recall@k, merchant agreement %, tier
   distribution, latency percentiles, est. $/query).
3. Determinism: two capture runs minutes apart → identical fixtures (modulo a
   documented tolerance); evaluate.py is pure given fixtures.
4. Self-parity sanity: `evaluate.py --backend chroma` scores ≈1.0 against its
   own fixtures.
5. Interface minimalism: consumers need only `search()` / `get_vector()`;
   fake and real backends swap without consumer changes.
6. Runtime: capture < 15 min; evaluate < 1 min offline.

After selection: the winning code recaptures fixtures ONCE as the canonical
reference set (four capture runs at different times differ slightly; only the
blessed set is committed).

## Round B — receipt_embeddings relocation (on merged Round A winner)

Relocate `receipt_chroma.embedding.formatting` and
`receipt_chroma.embedding.openai` into the `receipt_embeddings` package that
Round A created, leaving **back-compat shims** at the old import paths so
every existing consumer keeps working; regenerate the Swift parity fixtures
in the same PR.

Scope details:
- Prefer `git mv` so history follows the files.
- `receipt_chroma.embedding.formatting` / `.openai` become thin re-export
  shims (`from receipt_embeddings.… import *` plus explicit `__all__`) — the
  30+ cross-package importers must not change in this round.
- Swift parity: `receipt_ocr_swift/Scripts/generate_section_parity.py` and
  `generate_receipt_structure_parity.py` import the formatting surface; run
  them against the relocated code and regenerate the parity fixtures in-PR
  (CI byte-diffs fixtures regenerated from live Python — the anti-drift gate).

**Rubric (pre-committed):**
1. Relocation complete: both subpackages live in `receipt_embeddings`; zero
   `chromadb` imports anywhere in `receipt_embeddings`.
2. Shim completeness: every existing `receipt_chroma.embedding.{formatting,openai}`
   import site resolves unchanged; the full `receipt_chroma` test suite stays
   green; `receipt_upload` and `receipt_agent` suites unaffected.
3. Behavior identity: a test proves old-path and new-path modules are the
   same objects (or produce byte-identical outputs on fixed inputs); Swift
   parity fixtures regenerated twice are byte-identical.
4. **Documented reproducibility (hard requirement — Round A lesson)**: your
   verify commands must pass verbatim from a fresh checkout; an undocumented
   install/PYTHONPATH step is a failed rubric item, not a footnote.
5. Lean diff: moves, shims, fixture regen, and tests — no opportunistic
   refactors.
6. Final commit is `BAKEOFF_DONE.md` (self-report per rubric item + verify
   commands + not-verified-locally list). The judge's watcher keys on it —
   never commit it early.

Round-A-earned standing amendments (apply this round onward): graceful
degradation is scored wherever code touches live systems; any test double
must carry contract tests pinning it to the real dependency's validation
semantics; "green in my environment" claims that don't reproduce from a
fresh checkout score as failures.

## Round C+E1 — the engine (on merged Round B winner)

**Judge-provisioned facts (do NOT create/alter/delete vector indexes — ever):**
The dev table `ReceiptsTable-dc5be22` carries two live vector indexes, created
2026-08-31 (spec §3.2 amendment: distinct vector attribute names per item type
so each index sparsely selects its own items):

| Index | Vector attr | Dims | Distance | Inline filter | Projection (INCLUDE) |
|---|---|---|---|---|---|
| `line-embeddings` | `line_vector` | 1536 | COSINE | `section_type` (S) | text, merchant_name, place_id, image_id, receipt_id, line_id, row_line_ids, section_type |
| `word-embeddings` | `word_vector` | 1536 | COSINE | `label_status` (S) | text, merchant_name, image_id, receipt_id, line_id, word_id, label_status |

Environment: boto3 ≥ 1.43.64 required for `SearchVectors`/`VectorIndexUpdates`
(judge grading env has 1.43.84). Indexing is **asynchronous** after a write —
never assume read-after-write searchability. `SearchVectors` returns ≤ 100
results; filters are equality-only. Canonical similarity fixtures are S3-hosted
(see `tests/fixtures/similarity/CANONICAL_POINTER.md`; sha256-verified loader).

**Deliverables:**
1. Embedding-item entities per SPEC §3.1 — `RECEIPT_LINE_EMBEDDING` /
   `RECEIPT_WORD_EMBEDDING`: SK under the `RECEIPT#` prefix, `TYPE` set,
   vector attrs named exactly `line_vector` / `word_vector`, **no GSI1–4
   keys**, filter/projection attrs per the table above. Accessors in
   receipt_dynamo house style.
2. Embed-and-put writer in `receipt_embeddings`: OpenAI realtime →
   BatchWriteItem; idempotent (re-run writes nothing); per-item failures
   skip-and-report, never abort the batch.
3. Backfill script: golden receipts (+ `--extra-receipts` / `--limit`,
   Round A conventions) → dev embedding items; safe to re-run; ends with a
   written/skipped report and a searchability wait (poll until a sampled
   SearchVectors returns the new items, bounded, reported).
4. `DynamoVectorSearchClient` implementing the Round A `VectorSearchClient`
   protocol over `SearchVectors` — `evaluate.py --backend dynamo` becomes
   real. Service quotas as constants with contract tests pinning the fake
   (Round A standing amendment).
5. Merchant resolution behind `VECTOR_BACKEND=dynamodb|chroma` (default
   `chroma`): retrieval swapped via VectorSearchClient only; thresholds,
   tier logic, and corroboration gating byte-for-byte unchanged (SPEC §3.5a).

**Write discipline (hard rules):** dev table only; writes limited to embedding
items (`…#EMBEDDING` SKs) for golden/extras receipts; never delete or modify
non-embedding items; nothing touches prod; OpenAI spend capped by embedding
only what the backfill scope needs (reuse stored Chroma vectors where your
design can — an OpenAI-free backfill is a scored plus).

**Gates — phase 1 (all four, offline/free):** unit suite green from a fresh
checkout with documented steps; fake-backend parity vs the canonical fixtures:
recall@10 ≥ 0.85 overall (the canonical set's own offline-replay ceiling is
≈0.87 — corpus truncation), merchant agreement ≥ 98% vs fixture decisions;
graceful-degradation tests (missing vector, throttle, absent receipt).
**Phase 2 (judge-run, sequential, dev):** entrant's backfill (judge-capped
scope) → `evaluate.py --backend dynamo` vs canonical fixtures → recall@10,
merchant agreement, tier deltas, p50/p95 latency, cost estimate, idempotency
proof (second backfill run writes nothing). Judge wipes embedding items
between candidates.

Live behavior weighs heaviest this round (Round A lesson): a clean skip-report
run that misses a recall target beats a crash with perfect offline numbers.

## Later rounds

D (stream freshening leg), E2 (sections/proposer), E3 (QA + MCP +
similar_labeled_words), E4 (/receipt generators), X-cards — same cadence,
gates per AGENT_PLAN task cards. After a few rounds, single-agent assignment
for low-risk cards is a legitimate cost call.

## Reviewing a round

The judge assembles: four scorecards side-by-side, diffstat, new deps, and a
short design-choices note per candidate. Review that first; read the leading
candidate's diff in full; cherry-pick superior details from the others into
the winner before merging. Losers close with a one-line verdict note.

Merge rules: winner PR exits draft and merges into the stack (parents
`--merge`, leaves squash — never squash a stack parent).

## Round C appendix — SearchVectors wire-format notes (judge-verified 2026-08-31)

Shared with all entrants equally; discovered during the judge's smoke test:
- `SearchVector` is a **list of AttributeValue dicts** (`[{"N": "0.01"}, …]`) —
  not bare floats, not an `L`-wrapped AttributeValue.
- Results return under **`SearchResults`** (not `Items`).
- `ReturnConsumedCapacity` reports **`VectorSearchRequestBytes`** — request
  bytes are the billing meter, and a 1536-dim query is ~40KB of request; batch
  and serialize accordingly.
- Cold-call latency against the empty `line-embeddings` index: ~630ms from
  this Mac; expect in-region Lambda latency to be far lower.
- Index creation required the SearchSchema attribute in `AttributeDefinitions`
  (judge-handled; informational).

## Round C gate calibration (judge ruling, 2026-08-31)

The offline fake-replay **merchant agreement ceiling against the canonical
fixtures is 91.86%** — verified identical on pre-Round-C main, so it is
fixture/fake-determined (near-tie place_id flips on ~1e-6-distance duplicate
chain headers, plus one casing artifact; merchant-IDENTITY agreement is
98.8%). Phase-1 merchant gate is therefore: **≥ 91% on fake replay AND no
regression vs main's replay of the same fixtures.** The ≥ 98% bar applies to
the phase-2 populated-index dynamo evaluation. Recall@10 ≥ 0.85 unchanged.
Do not modify Round A fixtures or the fake to chase the offline number.

## Mode switch: distribution (post-Round-C ruling, 2026-08-31)

Round C's field converged (three entries with identical six-decimal offline
parity) — the design space is closed enough that tournament redundancy no
longer pays. Round C finishes as a tournament (phase 2 decides it); all
remaining cards are dealt **one per tool, in parallel**, each with its own
gate, judged the same way. Tournament mode returns only for a genuinely
design-open card.

**Dealt now (independent of Round C):**
- **X2 → grok**: delete the dormant `infra/embedding_step_functions/` batch
  tree (6 zip + 5 container Lambdas, 3 state machines, ECR repo, dashboard,
  second compaction impl) plus `combine_receipts_step_functions` and the
  `pattern_builder` Lambda. ⚠️ Drop each container Lambda's legacy-URN
  `aliases` in the same change (see SPEC §6 G). Fix the import-time breakages
  this exposes (SPEC §6 A items 3, 5). Gates: `pulumi preview` clean on BOTH
  stacks (read-only preview; do not deploy), full CI-relevant suites green,
  grep proof that nothing imports the deleted trees.
- **X4 → cursor**: dead-code sweep per SPEC §6 G / inventory-infra §G —
  `infra/chromadb_compaction/lambdas/processor/`, `dual_chroma_client.py`,
  the four unwired `simple_lambdas`, superseded monitoring builders,
  `conftest.py.bak`, orphaned chroma metric/trace/circuit-breaker emitters,
  the listed dead docs. NOTHING outside the documented dead list; no MCP or
  receipt_agent files (fenced for later cards). Gates: CI green, grep proofs
  per deleted item in the PR body.

**Dealt after Round C closes:** D (stream freshening) → claude;
E2 (sections/proposer ports) → codex; E3 (QA + MCP + similar_labeled_words,
absorbing X1's MCP-file deletions and X3's search.py items) and E4 (/receipt
generators + §5a) → assigned on availability. File fences: X2/X4 must not
touch files in receipt_agent/, scripts/receipt_mcp_server.py, or
infra/mcp_server_lambda/ — those belong to E3.

Standing protocol unchanged: branch `cards/<card>-<tool>`, commit-don't-push,
final commit replaces BAKEOFF_DONE.md wholesale.
