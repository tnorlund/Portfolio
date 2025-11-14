# Label Grouping Analysis: Would It Help Training?

## Current Label Grouping in UI

From `portfolio/components/ui/Figures/RandomReceiptWithLabels.tsx`:

```typescript
const LABEL_CATEGORIES = {
  merchant: {
    labels: ["MERCHANT_NAME", "STORE_HOURS", "PHONE_NUMBER", "WEBSITE", "LOYALTY_ID", "ADDRESS_LINE"],
  },
  transaction: {
    labels: ["DATE", "TIME", "PAYMENT_METHOD", "COUPON", "DISCOUNT"],
  },
  lineItems: {
    labels: ["PRODUCT_NAME", "QUANTITY", "UNIT_PRICE"],
  },
  totals: {
    labels: ["LINE_TOTAL", "SUBTOTAL", "TAX", "GRAND_TOTAL"],
  },
}
```

## Your Current Training Labels (7 labels)

- **Merchant**: MERCHANT_NAME, PHONE_NUMBER, ADDRESS_LINE (3 labels)
- **Transaction**: DATE, TIME (2 labels)
- **Line Items**: PRODUCT_NAME (1 label)
- **Currency**: AMOUNT (merged from LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL) (1 label)

## Would Label Grouping Help?

### ✅ **Yes, it could help!** Here's why:

1. **Label Relationships**: Labels in the same category are semantically related
   - DATE and TIME are both temporal
   - MERCHANT_NAME, PHONE_NUMBER, ADDRESS_LINE are all merchant info
   - The model could learn these relationships better with explicit category information

2. **Reduced Label Confusion**: Category information could help the model distinguish between similar labels
   - Currently: DATE vs TIME confusion
   - With categories: "This is in the Transaction category, so it's more likely DATE or TIME, not PRODUCT_NAME"

3. **Better Generalization**: Category-level understanding could help with rare labels
   - If the model knows something is "merchant info", it can narrow down to merchant labels

### Research Findings

- Hierarchical/multi-level label information can improve NER performance
- Category grouping helps models understand label relationships
- Multi-task learning (predicting category + label) can improve accuracy

## Implementation Approaches

### Option 1: Multi-Task Learning (Recommended)

Add a secondary task to predict the category alongside the label:

```python
# In trainer.py
class CategoryAwareTrainer:
    def __init__(self):
        self.label_to_category = {
            "MERCHANT_NAME": "merchant",
            "PHONE_NUMBER": "merchant",
            "ADDRESS_LINE": "merchant",
            "DATE": "transaction",
            "TIME": "transaction",
            "PRODUCT_NAME": "lineItems",
            "AMOUNT": "totals",
        }

    def compute_metrics(self, eval_pred):
        # Predict both label and category
        predictions, labels = eval_pred
        label_preds = predictions[:, :, :num_labels]  # Label predictions
        category_preds = predictions[:, :, num_labels:]  # Category predictions

        # Calculate F1 for labels
        label_f1 = f1_score(y_true, y_pred, ...)

        # Calculate accuracy for categories
        category_acc = accuracy_score(category_true, category_pred)

        return {"f1": label_f1, "category_acc": category_acc}
```

**Pros:**
- Explicitly teaches category relationships
- Can improve label prediction accuracy
- Relatively easy to implement

**Cons:**
- Requires modifying model architecture (add category classifier head)
- More complex training loop
- Need to balance label vs category loss

### Option 2: Category-Aware Label Embeddings

Initialize label embeddings with category information:

```python
# Initialize label embeddings with category similarity
def initialize_category_aware_embeddings(model, label_to_category):
    # Get label embeddings
    label_embeddings = model.classifier.weight.data

    # Average embeddings for labels in same category
    category_centroids = {}
    for label, category in label_to_category.items():
        if category not in category_centroids:
            category_centroids[category] = []
        label_idx = label2id[label]
        category_centroids[category].append(label_embeddings[label_idx])

    # Initialize new labels closer to category centroid
    for category, embeddings in category_centroids.items():
        centroid = torch.mean(torch.stack(embeddings), dim=0)
        for label, cat in label_to_category.items():
            if cat == category:
                label_idx = label2id[label]
                # Move embedding closer to centroid
                label_embeddings[label_idx] = 0.7 * label_embeddings[label_idx] + 0.3 * centroid
```

**Pros:**
- No architecture changes needed
- Simple to implement
- Helps model learn label relationships

**Cons:**
- Less explicit than multi-task learning
- May not help as much as explicit category prediction

### Option 3: Category Regularization Loss

Add a regularization term that encourages labels in the same category to have similar representations:

```python
def category_regularization_loss(model, label_to_category):
    # Get label embeddings
    label_embeddings = model.classifier.weight.data

    # Calculate intra-category similarity
    category_loss = 0
    for category in set(label_to_category.values()):
        category_labels = [l for l, c in label_to_category.items() if c == category]
        if len(category_labels) < 2:
            continue

        # Calculate pairwise distances within category
        category_embs = [label_embeddings[label2id[l]] for l in category_labels]
        for i, emb1 in enumerate(category_embs):
            for emb2 in category_embs[i+1:]:
                # Encourage similarity (small distance)
                category_loss += torch.norm(emb1 - emb2)

    return category_loss * 0.01  # Small weight
```

**Pros:**
- Encourages category relationships
- Easy to add to existing training

**Cons:**
- May conflict with label discrimination
- Harder to tune regularization weight

### Option 4: Hierarchical Label Prediction

First predict category, then predict label within category:

```python
# Two-stage prediction
def hierarchical_predict(model, inputs):
    # Stage 1: Predict category
    category_logits = model.category_classifier(inputs)
    category_pred = torch.argmax(category_logits, dim=-1)

    # Stage 2: Predict label within predicted category
    label_logits = model.label_classifier(inputs, category=category_pred)
    label_pred = torch.argmax(label_logits, dim=-1)

    return label_pred
```

**Pros:**
- Very explicit category information
- Can improve accuracy significantly

**Cons:**
- Requires significant architecture changes
- More complex training and inference
- Category errors propagate to label errors

## Recommendation

### **Start with Option 2 (Category-Aware Label Embeddings)**

**Why:**
1. **Easiest to implement** - No architecture changes needed
2. **Low risk** - Won't hurt performance, might help
3. **Quick to test** - Can implement in a few hours
4. **Good baseline** - If it helps, you can try more complex approaches

**Implementation Steps:**
1. Define label-to-category mapping
2. After model initialization, adjust label embeddings to be closer to category centroids
3. Train normally
4. Compare F1 scores

### **If Option 2 Helps, Try Option 1 (Multi-Task Learning)**

**Why:**
1. More explicit category information
2. Can measure category prediction accuracy
3. Research shows this can improve NER performance

## Expected Impact

Based on research:
- **Category-aware embeddings**: +1-3% F1 improvement possible
- **Multi-task learning**: +2-5% F1 improvement possible
- **Hierarchical prediction**: +3-7% F1 improvement possible (but more complex)

**For your 70% F1:**
- Could potentially reach **71-75% F1** with category information
- This would be significant given your 7-label complexity

## Current Training Labels Mapping

```python
LABEL_CATEGORIES = {
    "MERCHANT_NAME": "merchant",
    "PHONE_NUMBER": "merchant",
    "ADDRESS_LINE": "merchant",
    "DATE": "transaction",
    "TIME": "transaction",
    "PRODUCT_NAME": "lineItems",
    "AMOUNT": "totals",  # Merged from LINE_TOTAL, SUBTOTAL, TAX, GRAND_TOTAL
}
```

## Conclusion

**Yes, label grouping could help!** The category information provides valuable structure that the model could leverage. I'd recommend starting with category-aware label embeddings (Option 2) as it's the easiest to implement and test. If you see improvements, you can explore more complex approaches like multi-task learning.

The grouping you're already using in the UI is a good signal that these categories are meaningful - leveraging that in training could improve your model's understanding of label relationships.

