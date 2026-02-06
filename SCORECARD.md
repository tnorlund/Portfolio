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

| Q# | Question | Baseline | Hybrid Shaping |
|----|----------|----------|----------------|
| Q0 | How much did I spend on groceries last month? | D | B- |
| Q1 | What was my total spending at Costco? | F | A |
| Q2 | Show me all receipts with dairy products | C+ | B+ |
| Q3 | How much did I spend on coffee this year? | D | C+ |
| Q4 | What's my average grocery bill? | C | A- |
| Q5 | Find all receipts from restaurants | B | A- |
| Q6 | How much tax did I pay last quarter? | D | B- |
| Q7 | What was my largest purchase this month? | F | D |
| Q8 | Show receipts with items over $50 | C | B- |
| Q9 | How much did I spend on organic products? | D | C- |
| Q10 | What's the total for all gas station visits? | B | B |
| Q11 | Find all receipts with produce items | D | C+ |
| Q12 | How much did I spend on snacks? | D | D+ |
| Q13 | Show me pharmacy receipts from last week | C | B- |
| Q14 | What was my food spending trend over 6 months? | F | B- |
| Q15 | Find duplicate purchases across stores | C | D |
| Q16 | How much did I spend on beverages? | D | C- |
| Q17 | Show all receipts with discounts applied | D | C |
| Q18 | What's the breakdown by store category? | C | B |
| Q19 | Find receipts where I bought milk | B | B+ |
| Q20 | How much did I spend on household items? | B | D |
| Q21 | Show receipts from the past 7 days | F | D |
| Q22 | What's my monthly spending average? | F | C |
| Q23 | Find all breakfast item purchases | C | B |
| Q24 | How much did I spend on pet food? | B | A |
| Q25 | Show receipts with loyalty points earned | F | D |
| Q26 | What was my cheapest grocery trip? | F | A |
| Q27 | Find all receipts with returns or refunds | D | D |
| Q28 | How much did I spend on frozen foods? | D | C+ |
| Q29 | Show me spending patterns by day of week | F | B- |
| Q30 | What's my average tip at restaurants? | D | C |
| Q31 | Find receipts with handwritten notes | C | C |

## Run Log

| Run | Commit | Date | Branch | LangSmith Project | Notes |
|-----|--------|------|--------|-------------------|-------|
| Baseline | pre-fix | 2026-02-05 | chore/qa-evaluation | (prior run) | Before hybrid shaping fix |
| Hybrid Shaping | TBD | 2026-02-05 | chore/qa-evaluation | qa-eval-hybrid-shaping-20260205 | Added summary-tier + aggregates to shape/synthesize |

## Summary

| Run | A | B | C | D | F | Avg |
|-----|---|---|---|---|---|-----|
| Baseline (Q0-Q31) | 0 | 4 | 7 | 12 | 9 | D |
| Hybrid Shaping (Q0-Q31) | 5 | 11 | 9 | 7 | 0 | C+ |
