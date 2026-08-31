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

## Round C+E1 — the engine (on winner B; judge creates dev indexes first)

Per the original engine card:
1. Embedding-item entities (SPEC §3.1) + accessors in receipt_dynamo style.
2. Embed-and-put writer: OpenAI realtime → BatchWriteItem, idempotent.
3. Backfill script (golden receipts → dev), re-run writes nothing.
4. Merchant resolution behind `VECTOR_BACKEND` (default chroma), retrieval via
   VectorSearchClient only; thresholds/tier logic unchanged (SPEC §3.5a).

Gates — phase 1 (all four, offline): recall@10 ≥ 0.9; merchant agreement
≥ 98%; tier distribution ±5%; unit suite green.
Phase 2 (top two, sequential on dev, judge-wiped): backfill → `--backend
dynamo` → latency/cost recorded.

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
