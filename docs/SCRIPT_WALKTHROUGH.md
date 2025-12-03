# Script Walkthrough & Modularization Guide

## Current State Analysis

### 1. `visualize_final_clusters_cropped.py` (293 lines)

#### Current Flow:
```
main()
  └─> setup_environment()          # Load config
  └─> visualize_final_clusters_cropped()
       ├─> Load image entity & lines from DynamoDB
       ├─> recluster_receipt_lines()  # From split_receipt.py
       ├─> Download image from S3 (with fallback)
       ├─> For each cluster:
       │    ├─> calculate_receipt_bounds_from_lines()
       │    ├─> Warp image using affine transform
       │    ├─> For each line:
       │    │    ├─> get_line_corners_image_coords()
       │    │    ├─> Transform to warped receipt space
       │    │    └─> Draw polygon
       │    ├─> Add legend
       │    └─> Save image
```

#### Problems:
1. **Monolithic main function** - Does data loading, image processing, and visualization
2. **Coordinate transformation embedded** - Hard to reuse or test
3. **Image loading logic mixed in** - Should be separate utility

#### Proposed Structure:
```
visualize_final_clusters_cropped.py (main - 50 lines)
  └─> receipt_visualization.cluster_visualizer.visualize_final_clusters_cropped()
       ├─> receipt_processing.data_loading.load_image_data()
       ├─> receipt_processing.image_utils.load_image_with_fallback()
       ├─> receipt_visualization.bounds_calculation.calculate_receipt_bounds_from_lines()
       └─> receipt_visualization.cluster_visualizer.visualize_cluster()
            ├─> receipt_visualization.visualization.create_warped_image()
            ├─> receipt_visualization.coordinate_transforms.transform_lines_to_warped_space()
            └─> receipt_visualization.visualization.draw_bounding_boxes()
```

---

### 2. `split_receipt.py` (1731 lines!)

#### Current Flow:
```
main() (280 lines!)
  ├─> setup_environment()
  ├─> Load receipt data from DynamoDB
  ├─> recluster_receipt_lines()
  ├─> Download original image
  ├─> Find next receipt ID
  ├─> For each cluster:
  │    └─> create_split_receipt_records() (360 lines!)
  │         ├─> Filter lines/words/letters by cluster
  │         ├─> calculate_receipt_bounds()
  │         ├─> create_split_receipt_image()
  │         ├─> Upload to S3/CDN
  │         ├─> Create Receipt entity
  │         ├─> Transform & create ReceiptLines (100 lines)
  │         ├─> Transform & create ReceiptWords (100 lines)
  │         └─> Create ReceiptLetters (50 lines)
  ├─> save_records_locally()
  ├─> Save to DynamoDB
  ├─> export_receipt_ndjson_to_s3()
  ├─> create_embeddings_and_compaction_run()
  ├─> wait_for_compaction_complete()
  ├─> migrate_receipt_word_labels()
  └─> (Optional) delete_receipt_embeddings_from_chromadb()
```

#### Problems:

1. **`create_split_receipt_records()` is 360 lines:**
   - Does too many things
   - Coordinate transformation duplicated 3x (lines, words, letters)
   - Entity creation logic repeated

2. **`main()` is 280+ lines:**
   - Orchestrates entire workflow
   - Hard to test individual steps
   - Hard to reuse parts

3. **Coordinate transformation duplicated:**
   ```python
   # Same pattern repeated 3x:
   # 1. Transform from receipt space to image space
   line_copy.warp_transform(...)
   # 2. Convert to absolute image coordinates
   line_tl_img = {...}
   # 3. Transform from image space to new receipt space
   line_top_left_x, line_top_left_y = img_to_receipt_coord(...)
   ```

#### Proposed Structure:
```
split_receipt.py (main - 80 lines)
  └─> receipt_splitting.split_orchestrator.split_receipt()
       ├─> receipt_splitting.data_loading.load_all_receipt_data()
       ├─> receipt_splitting.clustering.recluster_receipt_lines()
       ├─> receipt_processing.image_utils.download_original_image()
       ├─> receipt_splitting.split_orchestrator.find_next_receipt_id()
       ├─> For each cluster:
       │    └─> receipt_splitting.entity_creation.create_split_receipt_entities()
       │         ├─> receipt_splitting.bounds_calculation.calculate_receipt_bounds_from_words()
       │         ├─> receipt_splitting.image_processing.create_and_upload_receipt_image()
       │         ├─> receipt_splitting.entity_creation.create_receipt_entity()
       │         ├─> receipt_splitting.entity_creation.create_receipt_lines()
       │         │    └─> receipt_splitting.coordinate_transforms.transform_entity_to_new_receipt_space()
       │         ├─> receipt_splitting.entity_creation.create_receipt_words()
       │         │    └─> receipt_splitting.coordinate_transforms.transform_entity_to_new_receipt_space()
       │         └─> receipt_splitting.entity_creation.create_receipt_letters()
       ├─> receipt_splitting.persistence.save_records_locally()
       ├─> receipt_splitting.persistence.save_to_dynamo()
       ├─> receipt_splitting.persistence.export_ndjson_to_s3()
       ├─> receipt_splitting.embeddings.create_embeddings_and_compaction_run()
       ├─> receipt_splitting.embeddings.wait_for_compaction_complete()
       ├─> receipt_splitting.label_migration.migrate_receipt_word_labels()
       └─> (Optional) receipt_splitting.embeddings.delete_receipt_embeddings()
```

---

## Key Refactoring Opportunities

### 1. Coordinate Transformation (Biggest Win)

**Current:** Duplicated 3x in `create_split_receipt_records()`

**After:**
```python
# receipt_splitting/coordinate_transforms.py
def transform_entity_to_new_receipt_space(
    entity: ReceiptLine | ReceiptWord | ReceiptLetter,
    original_receipt: Receipt,
    new_receipt_bounds: Dict,
    image_width: int,
    image_height: int,
) -> Dict[str, Any]:
    """Transform any receipt entity from original receipt space to new receipt space."""
    # Step 1: Transform to image space
    img_coords = transform_receipt_to_image_coords(
        entity, original_receipt, image_width, image_height
    )

    # Step 2: Transform to new receipt space
    receipt_coords = transform_image_to_receipt_coords(
        img_coords, new_receipt_bounds, image_width, image_height
    )

    return receipt_coords
```

**Usage:**
```python
# Instead of 100 lines per entity type:
line_coords = transform_entity_to_new_receipt_space(
    line, original_receipt, new_bounds, img_width, img_height
)
word_coords = transform_entity_to_new_receipt_space(
    word, original_receipt, new_bounds, img_width, img_height
)
```

### 2. Entity Creation (Remove Duplication)

**Current:** Similar patterns for lines, words, letters

**After:**
```python
# receipt_splitting/entity_creation.py
def create_receipt_line_entity(
    original_line: ReceiptLine,
    new_receipt_id: int,
    new_line_id: int,
    transformed_coords: Dict,
    new_receipt_bounds: Dict,
) -> ReceiptLine:
    """Create a ReceiptLine entity with transformed coordinates."""
    return ReceiptLine(
        receipt_id=new_receipt_id,
        line_id=new_line_id,
        text=original_line.text,
        top_left=transformed_coords["top_left"],
        top_right=transformed_coords["top_right"],
        bottom_left=transformed_coords["bottom_left"],
        bottom_right=transformed_coords["bottom_right"],
        # ... calculate bounding box from coords
    )

# Same pattern for words and letters
```

### 3. Main Function Simplification

**Current:** 280 lines doing everything

**After:**
```python
def main():
    args = parse_args()
    config = setup_environment()

    # Orchestrate the split
    result = split_receipt(
        image_id=args.image_id,
        original_receipt_id=args.original_receipt_id,
        config=config,
        dry_run=args.dry_run,
        skip_embedding=args.skip_embedding,
        delete_original=args.delete_original,
    )

    print_summary(result)
```

---

## Module Structure

```
receipt_processing/          # Shared utilities
├── __init__.py
├── config.py                # setup_environment()
├── image_utils.py           # S3 download, image loading
└── coordinate_utils.py     # Common coordinate helpers

receipt_visualization/       # Visualization module
├── __init__.py
├── image_loading.py
├── coordinate_transforms.py
├── bounds_calculation.py
├── visualization.py
└── cluster_visualizer.py

receipt_splitting/           # Split receipt module
├── __init__.py
├── data_loading.py
├── clustering.py           # recluster_receipt_lines()
├── coordinate_transforms.py # BIG WIN - remove duplication
├── bounds_calculation.py
├── image_processing.py
├── entity_creation.py      # Break down 360-line function
├── label_migration.py
├── persistence.py
├── embeddings.py
└── split_orchestrator.py
```

---

## Benefits Summary

1. **Remove 200+ lines of duplication** (coordinate transforms)
2. **Break 360-line function** into 5-6 focused functions
3. **Break 280-line main** into clear orchestration
4. **Enable unit testing** of individual components
5. **Share code** between visualization and splitting
6. **Improve readability** - each function has single responsibility

---

## Next Steps

1. **Review this plan** - Does this structure make sense?
2. **Start with shared utilities** - Lowest risk, highest reuse
3. **Then coordinate transforms** - Biggest code reduction
4. **Then entity creation** - Break down the 360-line function
5. **Finally main functions** - Simplify orchestration

