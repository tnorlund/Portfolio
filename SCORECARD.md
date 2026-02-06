# QA Evaluation Scorecard

Tracks answer quality across code iterations. Each column is a run tied to a specific commit/change.

## Grading Scale

| Grade | Meaning |
|-------|---------|
| A | Correct answer with proper evidence |
| B | Mostly correct, minor issues |
| C | Partially correct or missing context |
| D | Significant errors in answer or evidence |
| F | Wrong answer or critical data loss |

## Scores

| Q# | Question | Baseline | Hybrid Shaping | Synth Reasoning |
|----|----------|----------|----------------|-----------------|
| Q0 | How much did I spend on groceries last month? | D | B- | B- |
| Q1 | What was my total spending at Costco? | F | A | A |
| Q2 | Show me all receipts with dairy products | C+ | B+ | B+ |
| Q3 | How much did I spend on coffee this year? | D | C+ | B+ |
| Q4 | What's my average grocery bill? | C | A- | A |
| Q5 | Find all receipts from restaurants | B | A- | A- |
| Q6 | How much tax did I pay last quarter? | D | B- | B+ |
| Q7 | What was my largest purchase this month? | F | D | A- |
| Q8 | Show receipts with items over $50 | C | B- | B- |
| Q9 | How much did I spend on organic products? | D | C- | C |
| Q10 | What's the total for all gas station visits? | B | B | B |
| Q11 | Find all receipts with produce items | D | C+ | B- |
| Q12 | How much did I spend on snacks? | D | D+ | B |
| Q13 | Show me pharmacy receipts from last week | C | B- | B |
| Q14 | What was my food spending trend over 6 months? | F | B- | B+ |
| Q15 | Find duplicate purchases across stores | C | D | A- |
| Q16 | How much did I spend on beverages? | D | C- | B |
| Q17 | Show all receipts with discounts applied | D | C | B |
| Q18 | What's the breakdown by store category? | C | B | A- |
| Q19 | Find receipts where I bought milk | B | B+ | B+ |
| Q20 | How much did I spend on household items? | B | D | B- |
| Q21 | Show receipts from the past 7 days | F | D | D+ |
| Q22 | What's my monthly spending average? | F | C | B |
| Q23 | Find all breakfast item purchases | C | B | B+ |
| Q24 | How much did I spend on pet food? | B | A | A |
| Q25 | Show receipts with loyalty points earned | F | D | A |
| Q26 | What was my cheapest grocery trip? | F | A | A |
| Q27 | Find all receipts with returns or refunds | D | D | B+ |
| Q28 | How much did I spend on frozen foods? | D | C+ | B |
| Q29 | Show me spending patterns by day of week | F | B- | B- |
| Q30 | What's my average tip at restaurants? | D | C | B- |
| Q31 | Find receipts with handwritten notes | C | C | C+ |

## Iteration History

### Step 1: Baseline

The 5-node ReAct RAG workflow (plan → agent ↔ tools → shape → synthesize) with the original shape node. The shape node only processed detail-tier receipts (those with `words_by_line` data from `get_receipt`) and capped at 50. The synthesizer received shaped receipt data and the question, but no agent reasoning and no pre-computed aggregates.

**Problems identified:**
- Shape node dropped most receipts (only 14-50 survived shaping), causing massive data loss
- Synthesizer re-derived totals from the truncated shaped data, often producing wrong numbers
- No summary-tier receipts (from `get_receipt_summaries`) passed through to synthesis
- Pre-computed aggregates from tool calls were discarded

### Step 2: Hybrid Shaping

Added two-tier shaping and aggregate pass-through.

**Changes (`state.py`, `search.py`, `graph.py`):**
1. Added `tip`, `date`, `item_count` fields to `ReceiptSummary` state
2. Shape node now has two tiers: detail-tier (receipts with `words_by_line`) and summary-tier (lightweight summaries from `get_receipt_summaries` with merchant, date, total, tax, tip, item count)
3. Increased `MAX_RECEIPTS` from 50 to 200
4. `search.py` stores `summary_receipts` and `aggregates` in `state_holder` for the shape and synthesize nodes to consume
5. Synthesizer formats date, tip, and item count in receipt context; appends pre-computed aggregates

**Result:** D → C+ average. Eliminated all F grades (9 → 0). But 7 D-grade questions remained where the synthesizer contradicted the agent's correct reasoning.

### Step 3: Synth Reasoning

Passed the agent's last reasoning message to the synthesizer so it builds on the agent's analysis rather than re-deriving from scratch.

**Changes (`graph.py` only):**
1. Extract the last `AIMessage` content from `state.messages` (truncated to 3000 chars)
2. Updated `SYNTHESIZE_SYSTEM_PROMPT` to instruct the synthesizer to trust agent reasoning, respect item exclusions, and use receipt data for citations only
3. Include agent reasoning as an "Agent Analysis" section in the `HumanMessage` sent to the synthesizer

**Root cause addressed:** `synthesize_node` built its LLM prompt from shaped summaries, aggregates, and the question — but never read `state.messages`. The agent's AIMessage (with its analysis, tool results, item exclusions, and conclusions) was invisible to the synthesizer.

**Result:** C+ → B average. Key fixes:
- Q7 (D → A-): Synth now trusts agent's $413 finding instead of saying $50.65
- Q12 (D+ → B): Synth respects agent's chocolate chips exclusion
- Q15 (D → A-): Synth reports 5 duplicate categories instead of "no duplicates found"
- Q25 (D → A): Synth reports Vons loyalty points instead of "none found"
- Q27 (D → B+): Synth reports actual returns instead of denying them
- Q20 (D → B-): Synth no longer misreads Home Depot loyalty total as receipt total

### Remaining Issues

| Category | Questions | Description |
|----------|-----------|-------------|
| Date handling | Q21 | Agent fabricates date anchors; null-dated receipts pollute date-filtered queries |
| Search limits | Q9 | `search_product_lines` hits 100-item limit, missing majority of matches |
| Data quality | Q0 | $250 OCR misread, duplicate scans, null-dated receipts in date-filtered results |
| Agent recall | Q8, Q27 | Agent misses some matching receipts (Harbor Freight, DICK'S returns) |
| Day-of-week math | Q29 | Agent fabricates per-day spending breakdown |

## Run Log

| Run | Commit | Date | Branch | LangSmith Project | Notes |
|-----|--------|------|--------|-------------------|-------|
| Baseline | pre-fix | 2026-02-05 | chore/qa-evaluation | (prior run) | Before hybrid shaping fix |
| Hybrid Shaping | TBD | 2026-02-05 | chore/qa-evaluation | qa-eval-hybrid-shaping-20260205 | Added summary-tier + aggregates to shape/synthesize |
| Synth Reasoning | b307b1060 | 2026-02-06 | chore/qa-evaluation | qa-eval-synth-reasoning-v1 | Pass agent reasoning to synthesizer prompt |

## Summary

| Run | A | B | C | D | F | Avg |
|-----|---|---|---|---|---|-----|
| Baseline (Q0-Q31) | 0 | 4 | 7 | 12 | 9 | D |
| Hybrid Shaping (Q0-Q31) | 5 | 11 | 9 | 7 | 0 | C+ |
| Synth Reasoning (Q0-Q31) | 9 | 19 | 3 | 1 | 0 | B |
