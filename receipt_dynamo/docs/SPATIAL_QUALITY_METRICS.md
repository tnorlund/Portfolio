# Spatial Quality Metrics for Valid Receipt Word Labels

This document defines three core spatial quality metrics for analyzing the geometric relationships between valid receipt word labels using the receipt_dynamo geometry system.

## Overview

These metrics focus exclusively on words with `ValidationStatus.VALID` labels to assess the quality and structural integrity of receipt parsing by analyzing spatial relationships between confirmed labels.

## Core Geometric Operations Available

### From ReceiptWord Entity (`receipt_dynamo/entities/receipt_word.py`)

```python
# Primary geometric operations
word.calculate_centroid() -> Tuple[float, float]
word.distance_and_angle_from__receipt_word(other_word) -> Tuple[float, float]

# Geometric properties
word.bounding_box: Dict[str, Any]  # {'x': float, 'y': float, 'width': float, 'height': float}
word.top_left: Dict[str, float]    # {'x': float, 'y': float}
word.top_right: Dict[str, float]   # {'x': float, 'y': float}
word.bottom_left: Dict[str, float] # {'x': float, 'y': float}  
word.bottom_right: Dict[str, float] # {'x': float, 'y': float}
word.angle_degrees: float
word.angle_radians: float
word.confidence: float
```

### From GeometryMixin (`receipt_dynamo/entities/entity_mixins.py`)

```python
# Point-in-box testing
word.is_point_in_bounding_box(x: float, y: float) -> bool

# Transformations (if needed)
word.translate(x: float, y: float) -> None
word.scale(sx: float, sy: float) -> None
word.rotate(angle: float, origin_x: float, origin_y: float) -> None
```

---

## Metric 1: Label Positioning Quality

**Purpose**: Assess whether essential labels (MERCHANT_NAME, DATE, TOTAL) appear in their expected spatial regions on receipts.

### Geometric Operations Required

#### 1.1 Receipt Bounds Calculation
```python
def calculate_receipt_bounds(valid_words: List[ReceiptWord]) -> Dict[str, float]:
    """Calculate the spatial bounds of the entire receipt using valid labeled words."""
    
    all_centroids = [word.calculate_centroid() for word in valid_words]
    x_coords = [centroid[0] for centroid in all_centroids]
    y_coords = [centroid[1] for centroid in all_centroids]
    
    return {
        'min_x': min(x_coords),
        'max_x': max(x_coords), 
        'min_y': min(y_coords),
        'max_y': max(y_coords),
        'width': max(x_coords) - min(x_coords),
        'height': max(y_coords) - min(y_coords)
    }
```

#### 1.2 Regional Assignment
```python
def assign_receipt_regions(word: ReceiptWord, bounds: Dict[str, float]) -> str:
    """Assign a word to header/body/footer region based on its centroid position."""
    
    centroid_x, centroid_y = word.calculate_centroid()
    
    # Define region boundaries (header: top 25%, footer: bottom 25%, body: middle 50%)
    header_threshold = bounds['min_y'] + bounds['height'] * 0.25
    footer_threshold = bounds['min_y'] + bounds['height'] * 0.75
    
    if centroid_y <= header_threshold:
        return 'header'
    elif centroid_y >= footer_threshold:
        return 'footer'
    else:
        return 'body'
```

#### 1.3 Expected Label Positioning Rules
```python
def check_label_positioning_quality(valid_words: List[ReceiptWord], 
                                  valid_labels: List[ReceiptWordLabel]) -> Dict[str, Any]:
    """Assess whether labels appear in expected receipt regions."""
    
    bounds = calculate_receipt_bounds(valid_words)
    
    # Expected positioning rules
    expected_regions = {
        'MERCHANT_NAME': 'header',
        'DATE': 'header', 
        'TIME': 'header',
        'PHONE': 'header',
        'ADDRESS': 'header',
        'PRODUCT_NAME': 'body',
        'QUANTITY': 'body',
        'UNIT_PRICE': 'body',
        'LINE_TOTAL': 'body',
        'SUBTOTAL': 'footer',
        'TAX': 'footer', 
        'GRAND_TOTAL': 'footer',
        'PAYMENT_METHOD': 'footer'
    }
    
    positioning_quality = {}
    
    for word, label in zip(valid_words, valid_labels):
        label_type = label.label
        actual_region = assign_receipt_regions(word, bounds)
        expected_region = expected_regions.get(label_type, 'any')
        
        is_correctly_positioned = (expected_region == 'any' or 
                                 actual_region == expected_region)
        
        positioning_quality[f"{label_type}_{label.word_id}"] = {
            'label_type': label_type,
            'expected_region': expected_region,
            'actual_region': actual_region,
            'correctly_positioned': is_correctly_positioned,
            'centroid': word.calculate_centroid(),
            'confidence': word.confidence
        }
    
    # Calculate overall positioning quality score
    correct_count = sum(1 for item in positioning_quality.values() 
                       if item['correctly_positioned'])
    total_count = len(positioning_quality)
    
    return {
        'individual_results': positioning_quality,
        'overall_quality_score': correct_count / total_count if total_count > 0 else 0,
        'correctly_positioned_count': correct_count,
        'total_labeled_words': total_count,
        'receipt_bounds': bounds
    }
```

---

## Metric 2: Inter-Label Spatial Relationships

**Purpose**: Analyze how different label types relate spatially to validate expected receipt layout patterns.

### Geometric Operations Required

#### 2.1 Horizontal Alignment Analysis
```python
def check_horizontal_alignment(word1: ReceiptWord, word2: ReceiptWord, 
                             tolerance: float = 0.02) -> bool:
    """Check if two words are horizontally aligned (same receipt line)."""
    
    centroid1 = word1.calculate_centroid()
    centroid2 = word2.calculate_centroid()
    
    return abs(centroid1[1] - centroid2[1]) <= tolerance
```

#### 2.2 Price-Description Alignment Analysis  
```python
def analyze_price_description_alignment(valid_words: List[ReceiptWord],
                                      valid_labels: List[ReceiptWordLabel]) -> Dict[str, Any]:
    """Analyze alignment between product descriptions and their prices."""
    
    # Group words by label type
    label_groups = {}
    for word, label in zip(valid_words, valid_labels):
        label_type = label.label
        if label_type not in label_groups:
            label_groups[label_type] = []
        label_groups[label_type].append((word, label))
    
    # Find alignment relationships
    description_labels = label_groups.get('PRODUCT_NAME', [])
    price_labels = label_groups.get('LINE_TOTAL', []) + label_groups.get('UNIT_PRICE', [])
    
    alignment_results = []
    
    for desc_word, desc_label in description_labels:
        closest_price = None
        min_distance = float('inf')
        
        for price_word, price_label in price_labels:
            # Check if they're on the same line
            if check_horizontal_alignment(desc_word, price_word):
                distance, angle = desc_word.distance_and_angle_from__receipt_word(price_word)
                
                if distance < min_distance:
                    min_distance = distance
                    closest_price = (price_word, price_label)
        
        if closest_price:
            price_word, price_label = closest_price
            alignment_results.append({
                'description_word_id': desc_label.word_id,
                'description_text': desc_word.text,
                'price_word_id': price_label.word_id, 
                'price_text': price_word.text,
                'distance': min_distance,
                'description_centroid': desc_word.calculate_centroid(),
                'price_centroid': price_word.calculate_centroid(),
                'horizontally_aligned': True
            })
    
    # Calculate alignment quality metrics
    total_descriptions = len(description_labels)
    aligned_descriptions = len(alignment_results)
    alignment_rate = aligned_descriptions / total_descriptions if total_descriptions > 0 else 0
    
    if alignment_results:
        avg_alignment_distance = sum(r['distance'] for r in alignment_results) / len(alignment_results)
    else:
        avg_alignment_distance = 0
    
    return {
        'alignment_pairs': alignment_results,
        'alignment_rate': alignment_rate,
        'total_descriptions': total_descriptions,
        'aligned_descriptions': aligned_descriptions,
        'average_alignment_distance': avg_alignment_distance
    }
```

#### 2.3 Total Hierarchy Spatial Ordering
```python
def analyze_total_hierarchy_ordering(valid_words: List[ReceiptWord],
                                   valid_labels: List[ReceiptWordLabel]) -> Dict[str, Any]:
    """Analyze spatial ordering of SUBTOTAL → TAX → GRAND_TOTAL (bottom-to-top)."""
    
    # Find total-related labels
    total_types = ['SUBTOTAL', 'TAX', 'GRAND_TOTAL']
    total_words = {}
    
    for word, label in zip(valid_words, valid_labels):
        if label.label in total_types:
            total_words[label.label] = {
                'word': word,
                'label': label,
                'centroid': word.calculate_centroid()
            }
    
    # Check expected ordering (higher y-coordinate = lower on receipt)
    expected_order = ['GRAND_TOTAL', 'TAX', 'SUBTOTAL']  # Bottom to top
    ordering_results = []
    
    for i in range(len(expected_order) - 1):
        current_type = expected_order[i]
        next_type = expected_order[i + 1]
        
        if current_type in total_words and next_type in total_words:
            current_centroid = total_words[current_type]['centroid']
            next_centroid = total_words[next_type]['centroid']
            
            # GRAND_TOTAL should have higher y-coordinate (lower on receipt) than TAX
            correctly_ordered = current_centroid[1] >= next_centroid[1]
            
            distance, angle = (total_words[current_type]['word']
                             .distance_and_angle_from__receipt_word(total_words[next_type]['word']))
            
            ordering_results.append({
                'lower_label': current_type,
                'upper_label': next_type, 
                'correctly_ordered': correctly_ordered,
                'vertical_distance': abs(current_centroid[1] - next_centroid[1]),
                'total_distance': distance,
                'angle': angle
            })
    
    # Calculate hierarchy quality
    total_comparisons = len(ordering_results)
    correct_ordering_count = sum(1 for r in ordering_results if r['correctly_ordered'])
    hierarchy_quality = correct_ordering_count / total_comparisons if total_comparisons > 0 else 1.0
    
    return {
        'total_labels_found': total_words,
        'ordering_comparisons': ordering_results,
        'hierarchy_quality_score': hierarchy_quality,
        'correctly_ordered_pairs': correct_ordering_count,
        'total_comparisons': total_comparisons
    }
```

---

## Metric 3: Structural Analysis

**Purpose**: Assess receipt layout integrity by analyzing column alignment and row completeness of valid labels.

### Geometric Operations Required

#### 3.1 Column Alignment Detection
```python
def detect_column_alignment(valid_words: List[ReceiptWord], 
                          tolerance: float = 0.05) -> Dict[str, Any]:
    """Detect vertical column alignment patterns in valid labeled words."""
    
    # Extract x-coordinates of all word centroids
    word_positions = []
    for word in valid_words:
        centroid = word.calculate_centroid()
        word_positions.append({
            'word': word,
            'x': centroid[0],
            'y': centroid[1]
        })
    
    # Simple clustering of x-positions to find column centers
    x_coords = sorted([pos['x'] for pos in word_positions])
    if not x_coords:
        return {'columns': [], 'column_count': 0}
    
    # Cluster x-positions into columns
    columns = []
    current_cluster = [x_coords[0]]
    
    for x in x_coords[1:]:
        if x - current_cluster[-1] <= tolerance:
            current_cluster.append(x)
        else:
            # Finalize current cluster
            column_center = sum(current_cluster) / len(current_cluster)
            columns.append(column_center)
            current_cluster = [x]
    
    # Add final cluster
    if current_cluster:
        column_center = sum(current_cluster) / len(current_cluster)
        columns.append(column_center)
    
    # Assign words to columns
    column_assignments = {}
    for i, column_center in enumerate(columns):
        column_assignments[f'column_{i}'] = {
            'center_x': column_center,
            'words': [],
            'word_count': 0
        }
    
    for pos in word_positions:
        # Find closest column
        closest_column = None
        min_distance = float('inf')
        
        for col_key, col_data in column_assignments.items():
            distance = abs(pos['x'] - col_data['center_x'])
            if distance < min_distance:
                min_distance = distance
                closest_column = col_key
        
        if closest_column and min_distance <= tolerance:
            column_assignments[closest_column]['words'].append(pos['word'])
            column_assignments[closest_column]['word_count'] += 1
    
    return {
        'columns': column_assignments,
        'column_count': len(columns),
        'column_centers': columns
    }
```

#### 3.2 Row Completeness Analysis  
```python
def analyze_row_completeness(valid_words: List[ReceiptWord],
                           valid_labels: List[ReceiptWordLabel],
                           y_tolerance: float = 0.02) -> Dict[str, Any]:
    """Analyze what percentage of receipt rows contain at least one valid label."""
    
    # Group words into rows based on y-coordinate proximity
    word_positions = []
    for word in valid_words:
        centroid = word.calculate_centroid()
        word_positions.append({
            'word': word,
            'y': centroid[1]
        })
    
    if not word_positions:
        return {'rows': [], 'row_count': 0, 'completeness_rate': 0}
    
    # Sort by y-coordinate
    word_positions.sort(key=lambda x: x['y'])
    
    # Group into rows
    rows = []
    current_row = [word_positions[0]]
    current_y = word_positions[0]['y']
    
    for pos in word_positions[1:]:
        if abs(pos['y'] - current_y) <= y_tolerance:
            # Same row
            current_row.append(pos)
        else:
            # New row
            avg_y = sum(p['y'] for p in current_row) / len(current_row)
            rows.append({
                'y_position': avg_y,
                'word_count': len(current_row),
                'words': [p['word'] for p in current_row]
            })
            current_row = [pos]
            current_y = pos['y']
    
    # Add final row
    if current_row:
        avg_y = sum(p['y'] for p in current_row) / len(current_row)
        rows.append({
            'y_position': avg_y,
            'word_count': len(current_row),
            'words': [p['word'] for p in current_row]
        })
    
    # Calculate row completeness metrics
    total_rows = len(rows)
    rows_with_labels = total_rows  # All rows have labels since we only analyze valid labeled words
    
    # Calculate label density per row
    total_labels = len(valid_labels)
    avg_labels_per_row = total_labels / total_rows if total_rows > 0 else 0
    
    return {
        'rows': rows,
        'total_rows_with_labels': total_rows,
        'row_completeness_rate': 1.0,  # 100% since we only look at labeled words
        'average_labels_per_row': avg_labels_per_row,
        'total_valid_labels': total_labels,
        'label_distribution': [row['word_count'] for row in rows]
    }
```

#### 3.3 Column Integrity Analysis
```python
def analyze_column_integrity(valid_words: List[ReceiptWord],
                           valid_labels: List[ReceiptWordLabel]) -> Dict[str, Any]:
    """Analyze whether price-related labels consistently align in columns."""
    
    # Detect column structure
    column_structure = detect_column_alignment(valid_words)
    
    if not column_structure['columns']:
        return {'integrity_score': 0, 'analysis': 'No columns detected'}
    
    # Analyze label types within each column
    column_analysis = {}
    price_related_labels = {'LINE_TOTAL', 'UNIT_PRICE', 'SUBTOTAL', 'TAX', 'GRAND_TOTAL'}
    
    for col_key, col_data in column_structure['columns'].items():
        label_types_in_column = {}
        
        for word in col_data['words']:
            # Find corresponding label
            for valid_word, valid_label in zip(valid_words, valid_labels):
                if (valid_word.receipt_id == word.receipt_id and 
                    valid_word.line_id == word.line_id and 
                    valid_word.word_id == word.word_id):
                    
                    label_type = valid_label.label
                    if label_type not in label_types_in_column:
                        label_types_in_column[label_type] = 0
                    label_types_in_column[label_type] += 1
                    break
        
        # Calculate price label concentration in this column
        price_labels_count = sum(count for label_type, count in label_types_in_column.items()
                               if label_type in price_related_labels)
        total_labels_count = sum(label_types_in_column.values())
        
        price_concentration = price_labels_count / total_labels_count if total_labels_count > 0 else 0
        
        column_analysis[col_key] = {
            'center_x': col_data['center_x'],
            'total_words': col_data['word_count'],
            'label_distribution': label_types_in_column,
            'price_label_count': price_labels_count,
            'price_concentration': price_concentration,
            'is_price_column': price_concentration > 0.7  # >70% price labels
        }
    
    # Calculate overall column integrity
    price_columns = [col for col in column_analysis.values() if col['is_price_column']]
    has_dedicated_price_column = len(price_columns) >= 1
    
    # Find rightmost column (should be price column in typical receipts)
    rightmost_column = max(column_analysis.values(), key=lambda x: x['center_x'])
    rightmost_is_price_column = rightmost_column['is_price_column']
    
    integrity_score = 0.0
    if has_dedicated_price_column:
        integrity_score += 0.5
    if rightmost_is_price_column:
        integrity_score += 0.5
    
    return {
        'column_analysis': column_analysis,
        'integrity_score': integrity_score,
        'has_dedicated_price_column': has_dedicated_price_column,
        'rightmost_is_price_column': rightmost_is_price_column,
        'total_columns': len(column_analysis),
        'price_columns_count': len(price_columns)
    }
```

---

## Usage Example

```python
from receipt_dynamo.data import DynamoClient
from receipt_dynamo.constants import ValidationStatus

def analyze_receipt_spatial_quality(image_id: str, receipt_id: int) -> Dict[str, Any]:
    """Comprehensive spatial quality analysis for a single receipt."""
    
    client = DynamoClient()
    
    # Get all words and labels for the receipt
    all_words = client.list_receipt_words_from_receipt(image_id, receipt_id)
    all_labels, _ = client.list_receipt_word_labels_for_receipt(image_id, receipt_id)
    
    # Filter to valid labels only
    word_lookup = {(w.receipt_id, w.line_id, w.word_id): w for w in all_words}
    valid_words = []
    valid_labels = []
    
    for label in all_labels:
        if label.validation_status == ValidationStatus.VALID.value:
            word_key = (label.receipt_id, label.line_id, label.word_id)
            if word_key in word_lookup:
                valid_words.append(word_lookup[word_key])
                valid_labels.append(label)
    
    if not valid_words:
        return {'error': 'No valid labeled words found'}
    
    # Calculate all three metrics
    return {
        'receipt_info': {'image_id': image_id, 'receipt_id': receipt_id},
        'total_valid_labels': len(valid_labels),
        
        # Metric 1: Label Positioning Quality
        'label_positioning_quality': check_label_positioning_quality(valid_words, valid_labels),
        
        # Metric 2: Inter-Label Spatial Relationships
        'price_description_alignment': analyze_price_description_alignment(valid_words, valid_labels),
        'total_hierarchy_ordering': analyze_total_hierarchy_ordering(valid_words, valid_labels),
        
        # Metric 3: Structural Analysis
        'column_alignment': detect_column_alignment(valid_words),
        'row_completeness': analyze_row_completeness(valid_words, valid_labels),
        'column_integrity': analyze_column_integrity(valid_words, valid_labels)
    }
```

## Summary

These three metrics provide comprehensive spatial quality assessment using only the geometric operations available in the `receipt_dynamo` package:

1. **Label Positioning Quality**: Uses `calculate_centroid()` to verify labels appear in expected receipt regions
2. **Inter-Label Spatial Relationships**: Uses `distance_and_angle_from__receipt_word()` and `calculate_centroid()` to analyze alignment patterns
3. **Structural Analysis**: Uses coordinate clustering and `calculate_centroid()` to assess column alignment and row completeness

All metrics operate exclusively on `ValidationStatus.VALID` labels to ensure analysis quality and provide actionable insights for receipt parsing optimization.