# RandomReceiptWithLabels Component Review

## Overview

The `RandomReceiptWithLabels` component displays receipt images with overlaid labels showing semantic meaning (dates, totals, merchant names, etc.). After implementing the duplicate merge strategy that preserves multiple labels per word, we need to ensure the component handles this correctly.

## Current Implementation

### Strengths ✅

1. **Responsive Design**: Well-implemented mobile/desktop layouts
2. **Performance**: Uses `useOptimizedInView` for lazy loading
3. **Image Format Support**: Handles AVIF/WebP with fallbacks
4. **Loading States**: Good handling of loading and error states
5. **Animations**: Smooth transitions using `react-spring`
6. **Accessibility**: Proper alt text and semantic HTML

### Issues Found ⚠️

#### 1. **Multiple Labels Per Word Not Handled**

**Problem**: The `labelMap` uses `word_id` as the key, which means if a word has multiple labels (e.g., both `LINE_TOTAL` and `UNIT_PRICE`), only the last label will be shown.

```typescript
// Current implementation (lines 84-92)
const labelMap = useMemo(() => {
  if (!data?.labels) return new Map<number, ReceiptWordLabel>();
  const map = new Map<number, ReceiptWordLabel>();
  data.labels.forEach((label) => {
    map.set(label.word_id, label); // ❌ Overwrites previous labels
  });
  return map;
}, [data?.labels]);
```

**Impact**: After our merge strategy, words can have multiple labels (e.g., `ITEM_TOTAL` (INVALID) and `UNIT_PRICE` (VALID)). Only one will be displayed.

**Solution**: Change to store arrays of labels per word:
```typescript
const labelMap = useMemo(() => {
  if (!data?.labels) return new Map<number, ReceiptWordLabel[]>();
  const map = new Map<number, ReceiptWordLabel[]>();
  data.labels.forEach((label) => {
    if (!map.has(label.word_id)) {
      map.set(label.word_id, []);
    }
    map.get(label.word_id)!.push(label);
  });
  return map;
}, [data?.labels]);
```

#### 2. **SVG Overlay Only Shows One Label**

**Problem**: The SVG overlay (lines 283-326) only renders one label per word:
```typescript
const label = labelMap.get(word.word_id); // ❌ Gets only one label
if (!label) return null;
// ... renders single label
```

**Solution**: Render all labels for each word, potentially stacked or with different visual indicators:
```typescript
const labels = labelMap.get(word.word_id) || [];
if (labels.length === 0) return null;

return (
  <g key={`${word.receipt_id}-${word.word_id}`}>
    {labels.map((label, index) => (
      // Render each label with offset or different styling
    ))}
  </g>
);
```

#### 3. **Label List Key Uniqueness**

**Problem**: The key for list items uses `receipt_id-line_id-word_id` which would cause React key conflicts if multiple labels exist for the same word:
```typescript
<li key={`${label.receipt_id}-${label.line_id}-${label.word_id}`}>
```

**Solution**: Include the label type in the key:
```typescript
<li key={`${label.receipt_id}-${label.line_id}-${label.word_id}-${label.label}`}>
```

#### 4. **Label List Shows Only One Label Per Word**

**Problem**: The label list (lines 333-349, 384-400) iterates over `labelMap.values()` which only shows one label per word after our Map change.

**Solution**: Flatten the map to show all labels:
```typescript
{data.labels.map((label) => {
  // ... render each label
})}
```

#### 5. **No Validation Status Display**

**Problem**: The component doesn't show `validation_status` (VALID, INVALID, PENDING, etc.), which is important information after our merge strategy.

**Solution**: Add validation status indicators:
```typescript
<span className={styles.validationStatus} data-status={label.validation_status}>
  {label.validation_status === 'VALID' && '✓'}
  {label.validation_status === 'INVALID' && '✗'}
</span>
```

## Recommended Changes

### Priority 1: Handle Multiple Labels Per Word

1. **Update `labelMap`** to store arrays of labels
2. **Update SVG overlay** to render all labels per word
3. **Update label list** to show all labels
4. **Fix key uniqueness** to include label type

### Priority 2: Show Validation Status

1. Add visual indicators for validation status
2. Consider filtering options (show only VALID, show all, etc.)
3. Use different styling for INVALID labels

### Priority 3: UX Improvements

1. **Label conflicts**: When multiple labels exist, show them clearly
2. **Tooltips**: Show validation status and reasoning on hover
3. **Filtering**: Allow users to filter by validation status
4. **Stacking**: Handle overlapping labels in SVG overlay gracefully

## Code Changes Summary

### 1. Update labelMap (lines 84-92)
```typescript
const labelMap = useMemo(() => {
  if (!data?.labels) return new Map<number, ReceiptWordLabel[]>();
  const map = new Map<number, ReceiptWordLabel[]>();
  data.labels.forEach((label) => {
    if (!map.has(label.word_id)) {
      map.set(label.word_id, []);
    }
    map.get(label.word_id)!.push(label);
  });
  return map;
}, [data?.labels]);
```

### 2. Update labeledWords (lines 94-98)
```typescript
const labeledWords = useMemo(() => {
  if (!data?.words) return [];
  return data.words.filter((word) => labelMap.has(word.word_id));
}, [data?.words, labelMap]);
```

### 3. Update SVG overlay rendering (lines 283-326)
```typescript
{labelTransitions((style, word) => {
  const labels = labelMap.get(word.word_id) || [];
  if (labels.length === 0) return null;

  return (
    <g key={`${word.receipt_id}-${word.word_id}`}>
      {labels.map((label, index) => {
        // Calculate position with offset for multiple labels
        const offsetY = index * 15; // Stack labels vertically
        // ... render each label
      })}
    </g>
  );
})}
```

### 4. Update label list rendering (lines 333-349, 384-400)
```typescript
{data.labels.map((label) => {
  const word = data.words.find((w) => w.word_id === label.word_id);
  if (!word) return null;
  const color = getLabelColor(label.label);
  return (
    <li key={`${label.receipt_id}-${label.line_id}-${label.word_id}-${label.label}`}>
      <span
        className={styles.labelBadge}
        style={{ backgroundColor: color }}
      >
        {label.label}
      </span>
      {label.validation_status && (
        <span className={styles.validationStatus} data-status={label.validation_status}>
          {label.validation_status}
        </span>
      )}
      <span className={styles.wordText}>{word.text}</span>
    </li>
  );
})}
```

## Testing Considerations

1. **Test with multiple labels per word**: Verify all labels are displayed
2. **Test validation status display**: Ensure status indicators work
3. **Test SVG overlay**: Check that overlapping labels don't cause issues
4. **Test mobile layout**: Verify responsive behavior with multiple labels
5. **Test performance**: Ensure rendering multiple labels doesn't slow down animations

## Future Enhancements

1. **Label filtering**: Add UI to filter by validation status
2. **Label comparison**: Show why multiple labels exist (consolidation history)
3. **Label confidence**: Display confidence scores if available
4. **Interactive labels**: Click to see label details, reasoning, validation history

