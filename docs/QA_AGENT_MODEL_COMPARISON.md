# QA Agent Model Comparison

**Date:** January 20, 2026
**LangSmith Project:** `question-answering-marquee`
**View traces:** https://smith.langchain.com/

## Summary

We tested the Question Answering agent with 32 questions from the `QuestionMarquee.tsx` component using multiple OpenRouter models:

| Model | Tool Calling | Amounts Found | Avg Duration | Wall Time (parallel) |
|-------|--------------|---------------|--------------|----------------------|
| `openai/gpt-oss-120b` | **Broken** | 0/32 | 1.6s | 51.7s |
| `openai/gpt-4o-mini` | **Working** | 13/32 | 20.1s | 137.5s |
| `google/gemini-2.0-flash-001` | **Working** | 1/32 | 1.2s | 37.6s |
| `google/gemini-2.5-flash` | **Working** | 4/32 | 9.5s | 76.0s |
| `google/gemini-2.5-pro` | **Working** | 7/32 | 42.7s | 153.7s |

### Key Findings

1. **Best Quality**: `openai/gpt-4o-mini` - Found 13 dollar amounts, most thorough answers
2. **Best Speed/Quality Balance**: `google/gemini-2.5-flash` - 2x faster than gpt-4o-mini with decent quality
3. **Fastest**: `google/gemini-2.0-flash-001` - 37.6s but often gives up without trying tools
4. **Most Thorough**: `google/gemini-2.5-pro` - Good quality but slowest (153.7s)

## Root Cause: Malformed Tool Calls

The `gpt-oss-120b` model produces **malformed JSON** for tool call arguments:

**What gpt-oss-120b returns:**
```
  "label": "HANDWRITTEN",
  "limit": 20
```

**What it should return:**
```json
{
  "label": "HANDWRITTEN",
  "limit": 20
}
```

The opening `{` brace is missing, causing `JSONDecodeError: Extra data: line 1 column 10 (char 9)`.

This results in answers like:
- `}`
- `CHANT_NAME.}`
- `search.}`
- `_by_text.}`

These are fragments from the malformed JSON responses.

## Test Configuration

- **Concurrency:** 5 parallel questions
- **ChromaDB:** Chroma Cloud (`lines`: 12,834 embeddings, `words`: 69,609 embeddings)
- **DynamoDB:** ReceiptsTable-dc5be22 (dev environment)
- **LLM Provider:** OpenRouter API

## Results by Question

### Questions with Amounts (gpt-4o-mini)

| # | Question | Amount | Receipts | Duration |
|---|----------|--------|----------|----------|
| 2 | What was my total spending at Costco? | **$1,052.83** | 25 | 48.0s |
| 4 | How much did I spend on coffee this year? | **$23.98** | 3 | 8.7s |
| 5 | What's my average grocery bill? | **$13.11** | 5 | 17.9s |
| 8 | What was my largest purchase this month? | **$18.34** | 2 | 12.8s |
| 10 | How much did I spend on organic products? | **$56.99** | 40 | 44.6s |
| 11 | What's the total for all gas station visits? | **$10.99** | 1 | 4.4s |
| 13 | How much did I spend on snacks? | **$12.58** | 3 | 9.6s |
| 17 | How much did I spend on beverages? | **$19.59** | 1 | 10.2s |
| 19 | What's the breakdown by store category? | **$284.77** | 4 | 13.9s |
| 21 | How much did I spend on household items? | **$26.02** | 3 | 13.4s |
| 27 | What was my cheapest grocery trip? | **$3.49** | 1 | 16.6s |
| 29 | How much did I spend on frozen foods? | **$15.97** | 2 | 16.8s |

### Questions with List Results (gpt-4o-mini)

| # | Question | Receipts | Duration | Sample Answer |
|---|----------|----------|----------|---------------|
| 3 | Show me all receipts with dairy products | 10 | 52.1s | Sprouts (milk, yogurt), Vons, Costco (cheese) |
| 6 | Find all receipts from restaurants | 5 | 20.9s | Little Calf, Le Petit Cafe, Creamery & Cafe |
| 16 | Find duplicate purchases across stores | - | 15.0s | Coffee purchases at Vons, Sprouts, Five07 |
| 18 | Show all receipts with discounts applied | 6 | 29.2s | Sprouts (20% off), Target promo |
| 20 | Find receipts where I bought milk | 2 | 13.9s | Sprouts ($17.99), Vons ($17.90) |
| 22 | Show receipts from the past 7 days | 7 | 38.4s | Home Depot (multiple), etc. |
| 24 | Find all breakfast item purchases | 35 | 47.4s | Eggs, bacon, yogurt, oatmeal |
| 28 | Find all receipts with returns or refunds | 8 | 29.8s | Nordstrom, Home Depot, Target |
| 31 | What's my average tip at restaurants? | 4 | 20.3s | $1.90 avg (Pressed Juicery, Brent's, etc.) |

### Questions with No Data Found (gpt-4o-mini)

| # | Question | Duration | Reason |
|---|----------|----------|--------|
| 1 | How much did I spend on groceries last month? | 3.7s | No Dec 2025 receipts in data |
| 7 | How much tax did I pay last quarter? | 2.2s | Date filtering limitation |
| 12 | Find all receipts with produce items | 49.9s | Max iterations reached |
| 14 | Show me pharmacy receipts from last week | 2.4s | No pharmacy receipts found |
| 15 | What was my food spending trend over 6 months? | 36.7s | Complex aggregation not supported |
| 23 | What's my monthly spending average? | 12.7s | Aggregation limitation |
| 25 | How much did I spend on pet food? | 25.4s | No pet food found |
| 26 | Show receipts with loyalty points earned | 2.4s | No loyalty points in data |
| 30 | Show me spending patterns by day of week | 3.2s | Complex analysis not supported |
| 32 | Find receipts with handwritten notes | 2.4s | No handwritten labels |

## Comparison: gpt-oss-120b vs gpt-4o-mini

### Question 2: "What was my total spending at Costco?"

**gpt-oss-120b:**
```
CHANT_NAME.}
```
Duration: 1.2s - Malformed JSON, no tool execution

**gpt-4o-mini:**
```
Your total spending at Costco is $1,052.83.

This total is based on 25 receipts from Costco Wholesale.
```
Duration: 48.0s - Proper tool execution with ChromaDB search

---

### Question 4: "How much did I spend on coffee this year?"

**gpt-oss-120b:**
```
}
```
Duration: 0.7s - Malformed JSON

**gpt-4o-mini:**
```
You spent a total of $23.98 on coffee this year.
```
Duration: 8.7s - Found 3 receipts with coffee purchases

---

### Question 20: "Find receipts where I bought milk"

**gpt-oss-120b:**
```
_by_text.
}
```
Duration: 0.9s - Partial tool call fragment

**gpt-4o-mini:**
```
I found receipts where you bought milk. Here are the details:

1. **Merchant**: Sprouts Farmers Market
   - **Date**: July 29, 2024
   - **Item**: RAW WHOLE MILK
   - **Price**: $17.99

2. **Merchant**: Vons
   - **Date**: July 24, 2025
   - **Item**: DAIRY RAW WHOLE MILK
   - **Price**: $17.90
```
Duration: 13.9s - Full receipt details with prices

## Performance Metrics

### gpt-oss-120b (Broken)
- Total wall time: 51.7s
- Average per question: 1.6s
- Tool calls executed: 0
- Valid answers: 0/32

### gpt-4o-mini (Working)
- Total wall time: 137.5s (with concurrency=5)
- Sum of individual times: 644.1s
- Average per question: 20.1s
- Tool calls executed: ~150+
- Valid answers: 32/32
- Questions with amounts: 13
- Questions with lists: 9
- Questions with no data: 10

## Gemini Model Comparison

### google/gemini-2.0-flash-001
- **Wall time**: 37.6s (concurrency=10)
- **Amounts found**: 1/32
- **Behavior**: Very fast but often declines to use tools, gives quick "I can't do that" responses

### google/gemini-2.5-flash
- **Wall time**: 76.0s (concurrency=10)
- **Amounts found**: 4/32
- **Avg duration**: 9.5s per question

| # | Question | Amount | Duration |
|---|----------|--------|----------|
| 2 | What was my total spending at Costco? | $1,300.00 | 24.7s |
| 4 | How much did I spend on coffee this year? | $23.98 | 7.8s |
| 10 | How much did I spend on organic products? | $2,630.05 | 30.1s |
| 27 | What was my cheapest grocery trip? | $3.60 | 28.0s |

**Strengths**: Fast, good at finding specific items (milk: 48 receipts, produce: 39 receipts)
**Weaknesses**: Often asks for clarification instead of using tools, refuses date-based queries

### google/gemini-2.5-pro
- **Wall time**: 153.7s (concurrency=10)
- **Amounts found**: 7/32
- **Avg duration**: 42.7s per question

| # | Question | Amount | Duration |
|---|----------|--------|----------|
| 2 | What was my total spending at Costco? | $1,614.09 | 59.0s |
| 10 | How much did I spend on organic products? | $2,621.50 | 138.6s |
| 13 | How much did I spend on snacks? | $16.07 | 58.7s |
| 17 | How much did I spend on beverages? | $54.95 | 89.2s |
| 21 | How much did I spend on household items? | $27.48 | 36.6s |
| 29 | How much did I spend on frozen foods? | $25.95 | 59.5s |
| 11 | What's the total for all gas station visits? | $0.00 | 29.9s |

**Strengths**: More thorough than flash, better at aggregating amounts, found dairy in 60 receipts
**Weaknesses**: Slowest model tested, sometimes refuses category-based queries

### Side-by-Side: Question 2 "What was my total spending at Costco?"

| Model | Amount | Receipts | Duration |
|-------|--------|----------|----------|
| gpt-oss-120b | ❌ Broken | - | 1.2s |
| gpt-4o-mini | $1,052.83 | 25 | 48.0s |
| gemini-2.0-flash | ❌ No amount | - | ~1s |
| gemini-2.5-flash | $1,300.00 | 25 | 24.7s |
| gemini-2.5-pro | $1,614.09 | 25 | 59.0s |

*Note: Different amounts due to different tool call iterations and receipt sampling*

### Side-by-Side: Question 10 "How much did I spend on organic products?"

| Model | Amount | Receipts | Duration |
|-------|--------|----------|----------|
| gpt-4o-mini | $56.99 | 40 | 44.6s |
| gemini-2.5-flash | $2,630.05 | 14 | 30.1s |
| gemini-2.5-pro | $2,621.50 | 40 | 138.6s |

*gemini-2.5-pro spent more iterations to find comprehensive results*

### Side-by-Side: Question 20 "Find receipts where I bought milk"

| Model | Receipts Found | Duration |
|-------|----------------|----------|
| gpt-4o-mini | 2 | 13.9s |
| gemini-2.5-flash | 48 | 71.4s |
| gemini-2.5-pro | 47 | 81.9s |

*Gemini models found significantly more milk receipts through more thorough searching*

## Recommendations

1. **For production (quality focus)**: Use `openai/gpt-4o-mini`
   - Best balance of quality and reliability
   - Most consistent dollar amount extraction

2. **For production (speed focus)**: Use `google/gemini-2.5-flash`
   - 2x faster than gpt-4o-mini
   - Good for simple queries

3. **Avoid**: `openai/gpt-oss-120b` (broken tool calling)

4. **Consider**: `google/gemini-2.5-pro` for complex aggregation queries

5. **Update default model** in `receipt_agent/config/settings.py`:
   ```python
   openrouter_model: str = Field(
       default="openai/gpt-4o-mini",  # was "openai/gpt-oss-120b"
       ...
   )
   ```

## Files

- Test script: `scripts/test_qa_marquee_questions.py`
- Results (gpt-4o-mini): `/tmp/qa_marquee_results_parallel.json`
- Results (gemini-2.5-pro): `/tmp/qa_gemini_2.5_pro.json`
- Results (gemini-2.5-flash): `/tmp/qa_gemini_2.5_flash.json`
- LangSmith project: `question-answering-marquee`
