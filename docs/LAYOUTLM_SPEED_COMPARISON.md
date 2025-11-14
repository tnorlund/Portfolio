# LayoutLM vs Ollama/LangGraph Speed Comparison

## Summary

**✅ Goal Achieved**: LayoutLM is **6-30x faster** than Ollama/LangGraph for labeling receipt words.

## Performance Metrics

### LayoutLM (Current Implementation)

**Inference Time:**
- **Warm inference**: 1-3 seconds
- **Cached API response**: 100-500ms (S3 read only)
- **On-demand (cold start)**: 10-30s model load + 500ms-2s inference

**Architecture:**
- Container-based Lambda with model caching in `/tmp`
- Background cache generator runs every 5 minutes
- API serves pre-computed results from S3
- CPU inference (acceptable for cached approach)

**Current Usage:**
- ✅ Visualization API (`/layoutlm_inference`)
- ❌ Not yet integrated into production pipeline

### Ollama/LangGraph (Current Production)

**Total Time:**
- **20-30 seconds** per receipt

**Architecture:**
- Two-phase parallel analysis:
  1. **Phase 1**: Currency analysis using Ollama `gpt-oss:120b`
     - Identifies: GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL
  2. **Phase 2**: Line item analysis using Ollama `gpt-oss:20b` (parallel)
     - Identifies: PRODUCT_NAME, QUANTITY, UNIT_PRICE
     - Runs in parallel for each LINE_TOTAL found
  3. **Combine results**: Merges labels and creates ReceiptWordLabel entities

**Current Usage:**
- ✅ Production pipeline (`_run_validation_async` in upload Lambda)
- ✅ Called after compaction completes
- ✅ Saves labels to DynamoDB

## Speed Improvement

| Scenario | LayoutLM | Ollama/LangGraph | Improvement |
|----------|----------|------------------|-------------|
| **Warm inference** | 1-3s | 20-30s | **6-30x faster** |
| **Cached API** | 100-500ms | 20-30s | **40-300x faster** |
| **Cold start** | 10-30s + 0.5-2s | 20-30s | Similar (but only first call) |

## Accuracy Comparison

### LayoutLM
- **Overall Accuracy**: 91.8% (0.918)
- **F1 Score**: 0.7175 (71.75%)
- **Per-label F1**:
  - DATE: ~100%
  - AMOUNT: ~70.6%
  - MERCHANT_NAME: High
  - ADDRESS: High
- **Label Set**: 4 simplified labels (MERCHANT_NAME, DATE, ADDRESS, AMOUNT)

### Ollama/LangGraph
- **Accuracy**: Not explicitly measured, but uses validation chain
- **Label Set**: Full label set (all CORE_LABELS)
- **Confidence**: Uses LLM reasoning and confidence scores

## Cost Comparison

### LayoutLM
- **Infrastructure**: Lambda (CPU) + S3 storage
- **Cost per inference**: ~$0.0001 (cached) to ~$0.001 (on-demand)
- **Model storage**: ~451MB in S3
- **No external API calls**: Self-contained

### Ollama/LangGraph
- **Infrastructure**: Lambda + Ollama API calls
- **Cost per inference**: Higher (multiple LLM API calls)
- **External dependencies**: Requires Ollama API availability
- **API calls**: 1-5+ LLM calls per receipt (depending on line items)

## Next Steps

### Immediate Actions
1. ✅ **Documentation**: This document
2. ✅ **Visualization**: LayoutLM inference API working
3. ⏭️ **Production Integration**: Replace Ollama/LangGraph with LayoutLM

### Production Integration Plan

**Option 1: Direct Replacement (Recommended)**
- Replace `_run_validation_async` call with LayoutLM inference
- Use same container-based Lambda approach
- Keep model warm with provisioned concurrency
- Expected speedup: 6-30x

**Option 2: Hybrid Approach**
- Use LayoutLM for 4-label set (MERCHANT_NAME, DATE, ADDRESS, AMOUNT)
- Use Ollama/LangGraph for remaining labels (PRODUCT_NAME, QUANTITY, etc.)
- Best of both worlds: speed + comprehensive labeling

**Option 3: Gradual Migration**
- Run both in parallel initially
- Compare results and accuracy
- Gradually shift traffic to LayoutLM
- Keep Ollama/LangGraph as fallback

## Implementation Details

### LayoutLM Inference Pipeline
1. **Cache Generator Lambda** (runs every 5 minutes)
   - Selects random receipt with VALID labels
   - Runs LayoutLM inference
   - Compares predictions vs ground truth
   - Uploads JSON to S3

2. **API Lambda** (serves cached results)
   - Downloads JSON from S3
   - Returns structured response
   - Response time: 100-500ms

### Ollama/LangGraph Pipeline
1. **Upload Lambda** triggers validation
2. **Waits for compaction** to complete
3. **Runs LangGraph workflow**:
   - Load receipt data
   - Phase 1: Currency analysis
   - Phase 2: Line item analysis (parallel)
   - Combine results
   - Save labels to DynamoDB
4. **Total time**: 20-30 seconds

## References

- `LAYOUTLM_INFERENCE_API_PLAN.md` - LayoutLM inference architecture
- `docs/architecture/EXECUTION_FLOW_EXPLANATION.md` - Ollama/LangGraph timing
- `docs/architecture/LANGGRAPH_ARCHITECTURE_REVIEW.md` - LangGraph structure
- `LAYOUTLM_TRAINING_STRATEGY.md` - Training results and accuracy

## Conclusion

LayoutLM successfully achieves the goal of **faster labeling** compared to Ollama/LangGraph. The 6-30x speed improvement, combined with 91.8% accuracy, makes it an excellent candidate for production use. The next step is to integrate LayoutLM into the production pipeline to replace or supplement the Ollama/LangGraph approach.

