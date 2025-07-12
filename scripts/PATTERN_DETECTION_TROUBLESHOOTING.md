# Pattern Detection Troubleshooting Guide

## Overview

This guide documents the debugging process and solutions for integrating pattern detection with the receipt labeling decision engine testing framework.

## Problem: MockWord Object Interface Mismatch

### Issue Description
Pattern detectors were failing when processing MockWord objects created from exported receipt data, causing the test harness to not properly label words through pattern detection.

### Root Cause
**Interface Mismatch**: The MockWord class in `test_decision_engine.py` didn't match the expected interface of the real `ReceiptWord` class used by pattern detectors.

**Specific Problem**: `calculate_centroid()` method signature mismatch
```python
# MockWord (INCORRECT)
def calculate_centroid(self):
    return {
        'x': self.x + self.width / 2,
        'y': self.y + self.height / 2
    }  # Returns dictionary

# ReceiptWord (EXPECTED)
def calculate_centroid(self) -> Tuple[float, float]:
    return (x, y)  # Returns tuple
```

### Error Manifestation
Pattern detectors use tuple unpacking in `base.py`:
```python
word_x, word_y = word.calculate_centroid()  # Expects (x, y) tuple
```

When MockWord returned a dictionary, this caused:
- `TypeError: cannot unpack non-sequence` 
- Pattern detection silently failing
- Zero pattern matches despite valid data

### Solution
**Fixed MockWord.calculate_centroid()** to return tuple instead of dictionary:

```python
# scripts/test_decision_engine.py lines 141-147
def calculate_centroid(self):
    """Calculate the centroid of the word's bounding box."""
    return (
        self.x + self.width / 2,
        self.y + self.height / 2
    )
```

### Verification
**Before Fix**:
- Pattern detection: 0 labels found
- Decision: GPT required (missing all essential labels)

**After Fix**:
- Pattern detection: 7/11 words labeled (Target receipt)
- Pattern detection: 4/6 words labeled (Walmart receipt)
- Currency detector: 6 labels
- DateTime detector: 1 label
- Quantity detector: 2 labels

## Technical Analysis

### Pattern Detector Requirements
Pattern detectors in the `receipt_label` package expect `ReceiptWord` objects with:

1. **Required Methods**:
   - `calculate_centroid() -> Tuple[float, float]` ⚠️ **Critical**
   
2. **Required Properties**:
   - `bounding_box` (dict with x, y, width, height)
   - `text` (string)
   - `word_id` (identifier)
   - `is_noise` (boolean)
   - `confidence` (float)

3. **Coordinate System**:
   - `x, y, width, height` for bounding box
   - Centroid calculation: `(x + width/2, y + height/2)`

### MockWord Implementation
The MockWord class successfully provides all required attributes:

```python
class MockWord:
    def __init__(self, data):
        self.word_id = data.get('word_id')
        self.text = data.get('text', '')
        self.x = data.get('x', 0)
        self.y = data.get('y', 0)
        self.width = data.get('width', 0)
        self.height = data.get('height', 0)
        self.bounding_box = {
            'x': self.x,
            'y': self.y,
            'width': self.width,
            'height': self.height
        }
        self.line_id = data.get('line_id')
        self.confidence = data.get('confidence', 0)
        self.is_noise = data.get('is_noise', False)
        self.receipt_id = data.get('receipt_id')
        self.image_id = data.get('image_id')
    
    def calculate_centroid(self):
        return (
            self.x + self.width / 2,
            self.y + self.height / 2
        )
```

## Debugging Process

### Step 1: Identify Pattern Detection Failure
**Symptoms**:
- Test results showed 0 pattern matches
- All receipts required GPT (100% rate)
- No labels from pattern detectors

### Step 2: Trace Pattern Detection Flow
**Investigation**:
1. Verified pattern detectors are being called
2. Checked that MockWord objects are being created
3. Found that pattern detection was silently failing

### Step 3: Compare Interfaces
**Discovery**:
- Real `ReceiptWord.calculate_centroid()` returns tuple
- MockWord was returning dictionary
- Pattern detectors use tuple unpacking

### Step 4: Implement Fix
**Solution**:
- Updated MockWord.calculate_centroid() to return tuple
- Verified compatibility with pattern detector expectations

### Step 5: Validate Results
**Testing**:
- Re-ran decision engine tests
- Confirmed pattern detection now working
- Verified realistic label coverage (60-70%)

## Best Practices for Mock Objects

### Interface Compatibility
When creating mock objects for testing:

1. **Match method signatures exactly**:
   ```python
   # Real object method signature
   def calculate_centroid(self) -> Tuple[float, float]:
   
   # Mock should match exactly
   def calculate_centroid(self) -> Tuple[float, float]:
   ```

2. **Test mock objects independently**:
   ```python
   # Verify mock matches expected interface
   mock_word = MockWord(test_data)
   centroid = mock_word.calculate_centroid()
   assert isinstance(centroid, tuple)
   assert len(centroid) == 2
   ```

3. **Document interface requirements**:
   - What methods are required?
   - What should they return?
   - Any special behavior expectations?

### Pattern Detection Testing
For pattern detection testing specifically:

1. **Coordinate Systems**: Ensure x, y, width, height are realistic
2. **Text Content**: Use real receipt text for accurate pattern matching
3. **Bounding Boxes**: Provide realistic spatial relationships
4. **Noise Flags**: Set `is_noise=false` for actual content words

## Future Improvements

### Automated Interface Validation
Consider adding interface validation to catch mismatches early:

```python
def validate_word_interface(word_obj):
    """Validate that word object matches ReceiptWord interface."""
    assert hasattr(word_obj, 'calculate_centroid')
    centroid = word_obj.calculate_centroid()
    assert isinstance(centroid, tuple)
    assert len(centroid) == 2
    # ... other validations
```

### Real Data Integration
For even better testing, consider:
1. **Export real ReceiptWord objects** as pickle files
2. **Use actual database entities** in test harness
3. **Minimize mocking** where possible

## Related Files

- `scripts/test_decision_engine.py` - Main test harness with MockWord
- `receipt_label/pattern_detection/base.py` - Pattern detector base class
- `receipt_dynamo/entities.py` - Real ReceiptWord implementation
- `scripts/DECISION_ENGINE_TESTING_RESULTS.md` - Test results documentation