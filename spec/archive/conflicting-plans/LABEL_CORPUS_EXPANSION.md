# Label Corpus Expansion Plan

## Current vs Proposed Label Set

### Current Labels (18 total)
```
MERCHANT_NAME, STORE_HOURS, PHONE_NUMBER, WEBSITE, LOYALTY_ID,
ADDRESS_LINE, DATE, TIME, PAYMENT_METHOD, COUPON, DISCOUNT,
PRODUCT_NAME, QUANTITY, UNIT_PRICE, LINE_TOTAL, SUBTOTAL,
TAX, GRAND_TOTAL
```

### Proposed Expanded Label Set (60+ labels)

#### Store Information
- `MERCHANT_NAME` (existing)
- `STORE_NUMBER`
- `STORE_HOURS` (existing)
- `PHONE_NUMBER` (existing)
- `WEBSITE` (existing)
- `ADDRESS_LINE` (existing)
- `CITY`
- `STATE`
- `ZIP_CODE`
- `COUNTRY`

#### Transaction Metadata
- `DATE` (existing)
- `TIME` (existing)
- `TRANSACTION_ID`
- `REGISTER_NUMBER`
- `CASHIER_NAME`
- `CASHIER_ID`
- `ORDER_NUMBER`
- `TABLE_NUMBER` (restaurants)

#### Item Details
- `PRODUCT_NAME` (existing)
- `PRODUCT_CODE`/`SKU`
- `PRODUCT_CATEGORY`
- `QUANTITY` (existing)
- `UNIT_PRICE` (existing)
- `LINE_TOTAL` (existing)
- `ITEM_DISCOUNT`
- `DEPARTMENT`
- `BARCODE`

#### Pricing & Totals
- `SUBTOTAL` (existing)
- `TAX` (existing)
- `TAX_RATE`
- `GRAND_TOTAL` (existing)
- `DISCOUNT` (existing)
- `COUPON` (existing)
- `COUPON_CODE`
- `SAVINGS_AMOUNT`
- `TIP_AMOUNT`
- `SERVICE_CHARGE`
- `DELIVERY_FEE`

#### Payment Information
- `PAYMENT_METHOD` (existing)
- `CARD_TYPE`
- `CARD_LAST_FOUR`
- `AUTH_CODE`
- `PAYMENT_REFERENCE`
- `CHANGE_DUE`
- `AMOUNT_TENDERED`
- `PAYMENT_STATUS`

#### Loyalty & Rewards
- `LOYALTY_ID` (existing)
- `MEMBER_NAME`
- `POINTS_EARNED`
- `POINTS_BALANCE`
- `REWARDS_EARNED`
- `MEMBERSHIP_LEVEL`

#### Receipt Metadata
- `RECEIPT_NUMBER`
- `RETURN_POLICY`
- `SURVEY_CODE`
- `SURVEY_URL`
- `PROMOTIONAL_MESSAGE`
- `LEGAL_TEXT`
- `THANK_YOU_MESSAGE`
- `SLOGAN`

#### Special Categories
- `VOID_ITEM`
- `REFUND_ITEM`
- `EXCHANGE_ITEM`
- `GIFT_RECEIPT_CODE`
- `RAIN_CHECK`

## Label Hierarchy Structure

```
LABELS
├── STORE_INFO
│   ├── IDENTIFICATION
│   │   ├── MERCHANT_NAME
│   │   ├── STORE_NUMBER
│   │   └── STORE_HOURS
│   ├── CONTACT
│   │   ├── PHONE_NUMBER
│   │   └── WEBSITE
│   └── LOCATION
│       ├── ADDRESS_LINE
│       ├── CITY
│       ├── STATE
│       └── ZIP_CODE
├── TRANSACTION
│   ├── TEMPORAL
│   │   ├── DATE
│   │   └── TIME
│   ├── IDENTIFIERS
│   │   ├── TRANSACTION_ID
│   │   ├── ORDER_NUMBER
│   │   └── REGISTER_NUMBER
│   └── PERSONNEL
│       ├── CASHIER_NAME
│       └── CASHIER_ID
├── ITEMS
│   ├── PRODUCT_INFO
│   │   ├── PRODUCT_NAME
│   │   ├── PRODUCT_CODE
│   │   └── PRODUCT_CATEGORY
│   └── PRICING
│       ├── QUANTITY
│       ├── UNIT_PRICE
│       ├── LINE_TOTAL
│       └── ITEM_DISCOUNT
├── FINANCIAL
│   ├── TOTALS
│   │   ├── SUBTOTAL
│   │   ├── GRAND_TOTAL
│   │   └── CHANGE_DUE
│   ├── ADJUSTMENTS
│   │   ├── TAX
│   │   ├── DISCOUNT
│   │   ├── TIP_AMOUNT
│   │   └── SERVICE_CHARGE
│   └── SAVINGS
│       ├── COUPON
│       ├── COUPON_CODE
│       └── SAVINGS_AMOUNT
├── PAYMENT
│   ├── METHOD
│   │   ├── PAYMENT_METHOD
│   │   └── CARD_TYPE
│   └── DETAILS
│       ├── CARD_LAST_FOUR
│       ├── AUTH_CODE
│       └── PAYMENT_REFERENCE
└── LOYALTY
    ├── ACCOUNT
    │   ├── LOYALTY_ID
    │   └── MEMBER_NAME
    └── REWARDS
        ├── POINTS_EARNED
        ├── POINTS_BALANCE
        └── REWARDS_EARNED
```

## Implementation Strategy

### Phase 0: Foundation & Migration Prep (Week 0)
1. **Migration Infrastructure**
   - Create `LEGACY_LABEL_MAPPING` for backward compatibility
   - Implement feature flags for gradual rollout
   - Set up A/B testing framework for old vs new labels

2. **Versioned Label Registry**
   ```python
   class LabelRegistry:
       VERSION_1 = "v1"  # Current 18 labels
       VERSION_2 = "v2"  # Expanded 60+ labels

       def __init__(self, version="v2", enable_legacy=True):
           self.version = version
           self.enable_legacy = enable_legacy
           self.labels = self._load_labels(version)
           self.categories = self._load_categories(version)
   ```

3. **Performance Framework**
   - Implement schema caching mechanism
   - Create label subset profiles (grocery, restaurant, retail)
   - Add metrics collection for label usage

### Phase 1: Core Expansion (Week 1)
1. Add essential missing labels (25-30 labels)
   - Priority: `TRANSACTION_ID`, `CASHIER_NAME`, `CARD_LAST_FOUR`
   - These address current field_extraction.py failures
2. Focus on common receipt patterns
3. Maintain backward compatibility via mapping

### Phase 2: Category Support (Week 2)
1. Implement label hierarchy with performance optimization
2. Add category-based validation with caching
3. Update GPT prompts for categories
4. Create label subset configurations for different receipt types

### Phase 3: Advanced Labels (Week 3)
1. Add specialized labels based on usage metrics
2. Support multi-word labels
3. Implement confidence scoring by category
4. Monitor model performance with expanded label set

### Phase 4: Dynamic Labels & Optimization (Week 4)
1. Allow custom label registration
2. Implement label learning from data
3. Create label usage analytics dashboard
4. Optimize based on performance metrics
5. Plan legacy label deprecation timeline

## Potential Challenges & Mitigation

### 1. Migration Complexity
- **Challenge**: Updating existing labeled data to new schema
- **Mitigation**: Use `LEGACY_LABEL_MAPPING` and dual-system approach

### 2. Model Retraining
- **Challenge**: GPT-3.5-turbo needs new prompts for 60+ labels
- **Mitigation**: Incremental training with label subsets, A/B testing

### 3. Performance Impact
- **Challenge**: Larger validation schemas = slower API calls
- **Mitigation**: Schema caching, label subsets by receipt type, lazy loading

### 4. Backward Compatibility
- **Challenge**: Existing integrations may break
- **Mitigation**: Version flags, gradual deprecation, compatibility layer

## Benefits

1. **Better Coverage**: Can accurately label 95%+ of receipt content
2. **Improved Accuracy**: More specific labels reduce ambiguity
3. **Enhanced Analytics**: Richer data for downstream processing
4. **Flexibility**: Hierarchical structure supports various receipt types
5. **Scalability**: Dynamic system can grow with new receipt formats
6. **Performance Tracking**: Built-in metrics for continuous improvement

## Success Metrics

- **Label Coverage**: % of receipt words successfully labeled (target: >95%)
- **Extraction Accuracy**: Success rate of field extraction by label type
- **API Performance**: Response time with expanded label set (<10% degradation)
- **Migration Success**: % of existing data successfully migrated
- **Model Confidence**: Average confidence scores by label category
