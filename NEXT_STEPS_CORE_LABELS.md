# Next Steps: Adding Additional CORE_LABELS

## Current Status ✅

### Implemented (7 labels)
- ✅ **Phase 1 (Currency)**: GRAND_TOTAL, TAX, SUBTOTAL, LINE_TOTAL
- ✅ **Phase 2 (Line Items)**: PRODUCT_NAME, QUANTITY, UNIT_PRICE

### Missing from CORE_LABELS (28 total, 21 to consider)

---

## Priority: DATE and TIME (Next Implementation)

### Why These First?
1. **Critical for receipt validation**
2. **Easy to detect** (top of receipt)
3. **High value** (every receipt has date/time)
4. **Low implementation effort** (Phase 3 similar to Phase 1)

### Implementation Plan

Following the detailed plan in `LANGGRAPH_EXTENSION_PLAN.md`:

**Steps:**
1. Create Phase 3 models (HeaderLabel, HeaderLabelType, Phase3Response)
2. Update state to include `header_labels`
3. Create `phase3_header_analysis` node
4. Add Phase 3 to graph between Phase 1 and Phase 2
5. Update combine_results to include header labels
6. Test and deploy

**Time estimate**: 4-5 hours

---

## Remaining CORE_LABELS to Consider

### High Priority (After DATE/TIME)
- **PHONE_NUMBER** - Useful for contact info
- **ADDRESS_LINE** - Sometimes different from metadata
- **PAYMENT_METHOD** - Useful for expense categorization

### Medium Priority
- **COUPON** - Marketing analysis
- **DISCOUNT** - Price analysis
- **LOYALTY_ID** - Membership tracking

### Skip (Low Value)
- **MERCHANT_NAME** - Already in ReceiptMetadata ✅
- **STORE_HOURS** - Rarely useful
- **WEBSITE** - Low value

---

## Recommended Action

**Next**: Implement Phase 3 for DATE and TIME extraction

Would you like me to:
1. Start implementing Phase 3 (DATE/TIME)?
2. Review the CORE_LABELS constants to understand the full scope?
3. Create a prioritization matrix for remaining labels?

