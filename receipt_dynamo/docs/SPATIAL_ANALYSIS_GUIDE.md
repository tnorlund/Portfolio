# Spatial Analysis of Valid Receipt Word Labels

This guide documents the efficient data access patterns for analyzing the spatial distribution of VALID word labels in receipts.

## Overview

The goal is to understand how words with valid labels relate to one another spatially - examining clustering patterns, relative positioning, and layout structure for quality assessment and optimization.

## Data Model

### ReceiptWord Entity
- **Location**: `receipt_dynamo/entities/receipt_word.py`
- **Primary Key**: `PK: IMAGE#{image_id}`, `SK: RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}`
- **GSI3 Key**: `GSI3PK: IMAGE#{image_id}#RECEIPT#{receipt_id:05d}`, `GSI3SK: WORD`
- **Geometric Data**: bounding_box, corner points (top_left, top_right, etc.), angles, centroids

### ReceiptWordLabel Entity  
- **Location**: `receipt_dynamo/entities/receipt_word_label.py`
- **Primary Key**: `PK: IMAGE#{image_id}`, `SK: RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}#LABEL#{label}`
- **validation_status**: `ValidationStatus.VALID`, `ValidationStatus.INVALID`, etc.

## Optimized Data Access Pattern

### Single Receipt Analysis

```python
from receipt_dynamo.data import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from typing import List, Tuple, Dict, Any

def get_spatial_analysis_data(image_id: str, receipt_id: int) -> Tuple[List[ReceiptWord], List[ReceiptWordLabel]]:
    """
    Get all necessary data for spatial analysis of a single receipt.
    
    Args:
        image_id: UUID of the receipt image
        receipt_id: Integer ID of the receipt
        
    Returns:
        Tuple of (all_words, all_labels) for the receipt
    """
    client = DynamoClient()
    
    # Step 1: Get ALL words for the receipt (includes geometric data)
    all_words = client.list_receipt_words_from_receipt(
        image_id=image_id,
        receipt_id=receipt_id
    )
    
    # Step 2: Get ALL labels for the receipt  
    all_labels, _ = client.list_receipt_word_labels_for_receipt(
        image_id=image_id,
        receipt_id=receipt_id
    )
    
    return all_words, all_labels

def analyze_valid_label_distribution(image_id: str, receipt_id: int) -> Dict[str, Any]:
    """
    Perform spatial analysis of VALID labels for a single receipt.
    
    Returns comprehensive spatial metrics for the receipt.
    """
    all_words, all_labels = get_spatial_analysis_data(image_id, receipt_id)
    
    # Create lookup for efficient joining
    word_lookup = {
        (w.receipt_id, w.line_id, w.word_id): w 
        for w in all_words
    }
    
    # Filter to only VALID labels and join with geometric data
    valid_labeled_words = []
    valid_labels = []
    
    for label in all_labels:
        if label.validation_status == ValidationStatus.VALID.value:
            word_key = (label.receipt_id, label.line_id, label.word_id)
            if word_key in word_lookup:
                word = word_lookup[word_key]
                valid_labeled_words.append(word)
                valid_labels.append(label)
    
    return compute_spatial_metrics(valid_labeled_words, valid_labels)
```

### Spatial Analysis Functions

```python
def compute_spatial_metrics(words: List[ReceiptWord], labels: List[ReceiptWordLabel]) -> Dict[str, Any]:
    """
    Compute comprehensive spatial metrics for valid labeled words.
    
    Uses the geometry mixins from ReceiptWord for spatial calculations.
    """
    if not words or not labels:
        return {"error": "No valid labeled words found"}
    
    # 1. Basic Coverage Metrics
    total_words = len(words)  # This would need to be passed from all_words
    coverage_rate = len(words) / total_words if total_words > 0 else 0
    
    # 2. Label Type Distribution
    label_counts = {}
    label_positions = {}
    
    for word, label in zip(words, labels):
        label_type = label.label
        
        # Count occurrences
        label_counts[label_type] = label_counts.get(label_type, 0) + 1
        
        # Store positions for spatial analysis
        if label_type not in label_positions:
            label_positions[label_type] = []
        
        centroid = word.calculate_centroid()
        label_positions[label_type].append({
            'word': word,
            'centroid': centroid,
            'bounding_box': word.bounding_box
        })
    
    # 3. Spatial Distribution Analysis
    spatial_analysis = {}
    
    for label_type, positions in label_positions.items():
        if len(positions) >= 2:
            # Calculate inter-label distances for this type
            distances = []
            for i in range(len(positions)):
                for j in range(i + 1, len(positions)):
                    word1 = positions[i]['word']
                    word2 = positions[j]['word']
                    distance, angle = word1.distance_and_angle_from__receipt_word(word2)
                    distances.append(distance)
            
            spatial_analysis[label_type] = {
                'count': len(positions),
                'avg_distance': sum(distances) / len(distances) if distances else 0,
                'min_distance': min(distances) if distances else 0,
                'max_distance': max(distances) if distances else 0,
                'positions': [(p['centroid'][0], p['centroid'][1]) for p in positions]
            }
    
    # 4. Receipt Layout Analysis
    layout_metrics = analyze_receipt_layout(words, labels)
    
    return {
        'receipt_id': words[0].receipt_id,
        'image_id': words[0].image_id,
        'coverage_rate': coverage_rate,
        'label_counts': label_counts,
        'spatial_distribution': spatial_analysis,
        'layout_metrics': layout_metrics,
        'total_valid_labels': len(labels)
    }

def analyze_receipt_layout(words: List[ReceiptWord], labels: List[ReceiptWordLabel]) -> Dict[str, Any]:
    """
    Analyze the overall layout structure using valid labeled words as anchor points.
    """
    # Calculate receipt bounds
    all_centroids = [word.calculate_centroid() for word in words]
    x_coords = [c[0] for c in all_centroids]
    y_coords = [c[1] for c in all_centroids]
    
    receipt_bounds = {
        'min_x': min(x_coords),
        'max_x': max(x_coords),
        'min_y': min(y_coords),
        'max_y': max(y_coords),
        'width': max(x_coords) - min(x_coords),
        'height': max(y_coords) - min(y_coords)
    }
    
    # Analyze vertical distribution (header/body/footer regions)
    height = receipt_bounds['height']
    header_threshold = receipt_bounds['min_y'] + height * 0.33
    footer_threshold = receipt_bounds['min_y'] + height * 0.67
    
    region_distribution = {'header': 0, 'body': 0, 'footer': 0}
    
    for word in words:
        centroid_y = word.calculate_centroid()[1]
        if centroid_y < header_threshold:
            region_distribution['header'] += 1
        elif centroid_y > footer_threshold:
            region_distribution['footer'] += 1
        else:
            region_distribution['body'] += 1
    
    return {
        'receipt_bounds': receipt_bounds,
        'region_distribution': region_distribution,
        'aspect_ratio': receipt_bounds['width'] / receipt_bounds['height'] if receipt_bounds['height'] > 0 else 0
    }
```

## Batch Processing for Multiple Receipts

```python
def analyze_multiple_receipts(receipt_list: List[Tuple[str, int]]) -> Dict[str, Any]:
    """
    Analyze spatial patterns across multiple receipts.
    
    Args:
        receipt_list: List of (image_id, receipt_id) tuples
        
    Returns:
        Aggregated spatial analysis across all receipts
    """
    all_results = []
    
    for image_id, receipt_id in receipt_list:
        try:
            result = analyze_valid_label_distribution(image_id, receipt_id)
            all_results.append(result)
        except Exception as e:
            print(f"Error analyzing receipt {image_id}/{receipt_id}: {e}")
            continue
    
    # Aggregate results
    return aggregate_spatial_metrics(all_results)

def aggregate_spatial_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate spatial metrics across multiple receipts."""
    
    if not results:
        return {"error": "No valid results to aggregate"}
    
    # Aggregate label type patterns
    label_type_stats = {}
    coverage_rates = []
    
    for result in results:
        coverage_rates.append(result.get('coverage_rate', 0))
        
        for label_type, spatial_data in result.get('spatial_distribution', {}).items():
            if label_type not in label_type_stats:
                label_type_stats[label_type] = {
                    'occurrences': 0,
                    'avg_distances': [],
                    'position_patterns': []
                }
            
            label_type_stats[label_type]['occurrences'] += 1
            label_type_stats[label_type]['avg_distances'].append(spatial_data['avg_distance'])
            label_type_stats[label_type]['position_patterns'].extend(spatial_data['positions'])
    
    return {
        'total_receipts_analyzed': len(results),
        'avg_coverage_rate': sum(coverage_rates) / len(coverage_rates),
        'label_type_statistics': label_type_stats,
        'overall_patterns': extract_overall_patterns(label_type_stats)
    }

def extract_overall_patterns(label_stats: Dict[str, Any]) -> Dict[str, Any]:
    """Extract high-level patterns from aggregated label statistics."""
    
    patterns = {}
    
    for label_type, stats in label_stats.items():
        if stats['avg_distances']:
            avg_spacing = sum(stats['avg_distances']) / len(stats['avg_distances'])
            patterns[label_type] = {
                'frequency': stats['occurrences'],
                'average_spacing': avg_spacing,
                'spatial_consistency': calculate_spatial_consistency(stats['position_patterns'])
            }
    
    return patterns

def calculate_spatial_consistency(positions: List[Tuple[float, float]]) -> float:
    """
    Calculate how consistently a label type appears in similar positions.
    
    Returns a consistency score between 0 and 1.
    """
    if len(positions) < 2:
        return 1.0
    
    # Calculate coefficient of variation for x and y coordinates
    x_coords = [pos[0] for pos in positions]
    y_coords = [pos[1] for pos in positions]
    
    x_mean = sum(x_coords) / len(x_coords)
    y_mean = sum(y_coords) / len(y_coords)
    
    x_variance = sum((x - x_mean) ** 2 for x in x_coords) / len(x_coords)
    y_variance = sum((y - y_mean) ** 2 for y in y_coords) / len(y_coords)
    
    x_cv = (x_variance ** 0.5) / x_mean if x_mean > 0 else float('inf')
    y_cv = (y_variance ** 0.5) / y_mean if y_mean > 0 else float('inf')
    
    # Convert coefficient of variation to consistency score (lower CV = higher consistency)
    consistency = 1.0 / (1.0 + (x_cv + y_cv) / 2.0)
    return min(consistency, 1.0)
```

## Performance Considerations

### Efficient Data Access
- **Single Query per Receipt**: `list_receipt_words_from_receipt()` and `list_receipt_word_labels_for_receipt()` use optimized DynamoDB queries
- **In-Memory Joining**: Create lookup dictionaries to efficiently join words and labels
- **Batch Processing**: Process multiple receipts in batches to amortize connection overhead

### Geometric Computation Optimization
- **Cached Centroids**: Use `SpatialWord` wrapper from `receipt_label/spatial/geometry_utils.py` for centroid caching
- **Selective Analysis**: Only compute expensive metrics (distances, angles) for valid labeled words
- **Spatial Indexing**: Consider using spatial data structures for large-scale analysis

## Usage Examples

### Single Receipt Analysis
```python
# Analyze spatial distribution for one receipt
result = analyze_valid_label_distribution(
    image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    receipt_id=1
)
print(f"Coverage: {result['coverage_rate']:.2%}")
print(f"Label types: {list(result['label_counts'].keys())}")
```

### Multiple Receipt Analysis
```python
# Analyze patterns across multiple receipts
receipts = [
    ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1),
    ("4a23cd87-1bef-4d12-93c9-d594eb4b9fe4", 2),
    # ... more receipts
]

aggregate_results = analyze_multiple_receipts(receipts)
print(f"Analyzed {aggregate_results['total_receipts_analyzed']} receipts")
print(f"Average coverage: {aggregate_results['avg_coverage_rate']:.2%}")
```

## Integration with Existing Systems

This spatial analysis integrates with:

- **Geometry Mixins** (`receipt_dynamo/entities/entity_mixins.py`): Uses existing geometric operations
- **Spatial Utils** (`receipt_label/spatial/geometry_utils.py`): Leverages spatial analysis infrastructure  
- **DynamoDB Operations** (`receipt_dynamo/data/`): Uses optimized query patterns
- **Validation Pipeline**: Can inform label quality assessment and improvement

## Future Enhancements

1. **Machine Learning Integration**: Use spatial patterns to improve automatic labeling
2. **Anomaly Detection**: Identify receipts with unusual spatial distributions
3. **Quality Scoring**: Develop spatial quality metrics for receipt processing
4. **Visualization**: Create spatial heatmaps and distribution charts