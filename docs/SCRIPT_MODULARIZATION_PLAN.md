# Script Modularization Plan

## Overview

Both `visualize_final_clusters_cropped.py` and `split_receipt.py` are monolithic scripts that need to be broken down into smaller, reusable modules.

---

## 1. `visualize_final_clusters_cropped.py` Analysis

### Current Structure (293 lines)

**Functions:**
- `get_image_from_s3()` - Utility (5 lines)
- `get_cluster_color()` - Utility (13 lines)
- `get_line_corners_image_coords()` - Coordinate conversion (21 lines)
- `calculate_receipt_bounds_from_lines()` - Complex bounds calculation (73 lines)
- `visualize_final_clusters_cropped()` - **Monolithic main function** (114 lines)
  - Loads data from DynamoDB
  - Downloads image from S3
  - Clusters lines
  - For each cluster:
    - Calculates bounds
    - Warps image
    - Draws bounding boxes
    - Adds legend
    - Saves image
- `main()` - CLI (18 lines)

### Issues

1. **`visualize_final_clusters_cropped()` does too much:**
   - Data loading
   - Image downloading (with fallback logic)
   - Clustering
   - Image warping
   - Visualization drawing
   - File I/O

2. **Coordinate transformation logic embedded:**
   - Line corner conversion
   - Affine transform application
   - Inverse transform calculation

3. **Image handling scattered:**
   - S3 download with fallbacks
   - Image warping
   - Drawing operations

### Proposed Modularization

**New Module: `receipt_visualization/`**

```
receipt_visualization/
├── __init__.py
├── image_loading.py          # S3 download, fallback logic
├── coordinate_transforms.py # OCR↔Image↔Warped space conversions
├── bounds_calculation.py     # Affine transform bounds from lines
├── visualization.py         # Drawing operations, legend
└── cluster_visualizer.py     # Main orchestrator
```

**Functions to Extract:**

1. **`image_loading.py`:**
   - `load_image_from_s3(bucket, key) -> PIL.Image`
   - `load_image_with_fallback(image_entity, raw_bucket) -> PIL.Image`

2. **`coordinate_transforms.py`:**
   - `line_corners_to_image_coords(line, img_width, img_height) -> List[Tuple]`
   - `image_coords_to_warped_receipt_coords(corners_img, affine_transform) -> List[Tuple]`
   - `invert_affine_transform(affine_transform) -> Tuple`

3. **`bounds_calculation.py`:**
   - `calculate_receipt_bounds_from_lines(cluster_lines, img_width, img_height) -> Dict`
   - (Already exists, just move it)

4. **`visualization.py`:**
   - `draw_line_bounding_boxes(draw, lines, color, img_width, img_height, affine_transform) -> None`
   - `add_legend(draw, cluster_id, num_lines, color) -> None`
   - `create_warped_image(original_image, bounds) -> PIL.Image`

5. **`cluster_visualizer.py`:**
   - `visualize_cluster(cluster_id, cluster_lines, original_image, img_width, img_height, output_path) -> None`
   - `visualize_final_clusters_cropped(image_id, output_dir, raw_bucket) -> None`

---

## 2. `split_receipt.py` Analysis

### Current Structure (1731 lines!)

**Functions:**
- `setup_environment()` - Config loading (61 lines)
- `recluster_receipt_lines()` - Clustering logic (120 lines)
- `calculate_receipt_bounds()` - Bounds from words (77 lines)
- `create_split_receipt_image()` - Image cropping (52 lines)
- `create_split_receipt_records()` - **MASSIVE** (360 lines!)
  - Filters lines/words/letters by cluster
  - Calculates bounds
  - Creates receipt image
  - Uploads to S3/CDN
  - Creates Receipt entity
  - Transforms and creates ReceiptLine entities
  - Transforms and creates ReceiptWord entities
  - Creates ReceiptLetter entities
- `migrate_receipt_word_labels()` - Label migration (32 lines)
- `save_records_locally()` - Local JSON save (49 lines)
- `export_receipt_ndjson_to_s3()` - NDJSON export (54 lines)
- `create_embeddings_and_compaction_run()` - Embedding creation (122 lines)
- `delete_receipt_embeddings_from_chromadb()` - ChromaDB cleanup (115 lines)
- `wait_for_compaction_complete()` - Compaction polling (98 lines)
- `main()` - **MASSIVE orchestrator** (280+ lines!)

### Issues

1. **`create_split_receipt_records()` is 360 lines doing:**
   - Data filtering
   - Bounds calculation
   - Image creation/upload
   - Receipt entity creation
   - Coordinate transformation (repeated 3x for lines/words/letters)
   - Entity creation (lines, words, letters)

2. **`main()` is 280+ lines orchestrating:**
   - Data loading
   - Clustering
   - Image downloading
   - Receipt ID finding
   - Record creation loop
   - Local save
   - DynamoDB save
   - NDJSON export
   - Embedding creation
   - Compaction waiting
   - Label addition
   - Original receipt deletion

3. **Coordinate transformation duplicated:**
   - Same logic repeated for ReceiptLine, ReceiptWord, ReceiptLetter
   - Complex transform chain: receipt space → image space → new receipt space

4. **Entity creation logic duplicated:**
   - Similar patterns for lines, words, letters

### Proposed Modularization

**New Module: `receipt_splitting/`**

```
receipt_splitting/
├── __init__.py
├── data_loading.py           # Load receipt data from DynamoDB
├── clustering.py            # Re-clustering logic (move from split_receipt)
├── coordinate_transforms.py # Receipt↔Image↔NewReceipt transforms
├── bounds_calculation.py    # Calculate receipt bounds from words/lines
├── image_processing.py      # Image cropping, S3 upload, CDN formats
├── entity_creation.py       # Create Receipt/ReceiptLine/ReceiptWord/ReceiptLetter
├── label_migration.py       # Migrate ReceiptWordLabels
├── persistence.py           # Local save, DynamoDB save
├── embeddings.py            # Embedding creation, compaction
└── split_orchestrator.py    # Main orchestrator
```

**Functions to Extract:**

1. **`data_loading.py`:**
   - `load_receipt_data(client, image_id, receipt_id) -> Dict`
   - `load_image_level_data(client, image_id) -> Dict`
   - `download_original_image(image_entity, raw_bucket) -> PIL.Image`

2. **`clustering.py`:**
   - `recluster_receipt_lines()` - (Move from split_receipt.py)

3. **`coordinate_transforms.py`:**
   - `transform_receipt_to_image_coords(entity, original_receipt, img_width, img_height) -> Dict`
   - `transform_image_to_receipt_coords(img_coords, new_receipt_bounds, img_width, img_height) -> Dict`
   - `transform_entity_to_new_receipt_space(entity, original_receipt, new_receipt_bounds, img_width, img_height) -> Dict`

4. **`bounds_calculation.py`:**
   - `calculate_receipt_bounds_from_words()` - (Move from split_receipt.py)
   - `calculate_receipt_bounds_from_lines()` - (Could share with visualization)

5. **`image_processing.py`:**
   - `create_split_receipt_image()` - (Move from split_receipt.py)
   - `upload_receipt_image_to_s3()` - Extract from create_split_receipt_records
   - `upload_receipt_to_cdn()` - Extract from create_split_receipt_records

6. **`entity_creation.py`:**
   - `create_receipt_entity(image_id, receipt_id, bounds, receipt_image, ...) -> Receipt`
   - `create_receipt_line_entity(line, new_receipt_id, transform_result) -> ReceiptLine`
   - `create_receipt_word_entity(word, new_receipt_id, transform_result) -> ReceiptWord`
   - `create_receipt_letter_entity(letter, new_receipt_id, ...) -> ReceiptLetter`
   - `create_split_receipt_entities(cluster_data, original_receipt, ...) -> Dict`

7. **`label_migration.py`:**
   - `migrate_receipt_word_labels()` - (Move from split_receipt.py)

8. **`persistence.py`:**
   - `save_records_locally()` - (Move from split_receipt.py)
   - `save_receipt_to_dynamo(client, receipt, lines, words, letters) -> None`
   - `export_receipt_ndjson_to_s3()` - (Move from split_receipt.py)

9. **`embeddings.py`:**
   - `create_embeddings_and_compaction_run()` - (Move from split_receipt.py)
   - `wait_for_compaction_complete()` - (Move from split_receipt.py)
   - `delete_receipt_embeddings_from_chromadb()` - (Move from split_receipt.py)

10. **`split_orchestrator.py`:**
    - `find_next_receipt_id(client, image_id) -> int`
    - `split_receipt(image_id, original_receipt_id, ...) -> Dict`
    - `main()` - Simplified CLI

---

## 3. Shared Utilities

Both scripts share common functionality that should be extracted:

**New Module: `receipt_processing/` (shared utilities)**

```
receipt_processing/
├── __init__.py
├── image_utils.py           # S3 download, image loading
├── coordinate_utils.py     # Common coordinate conversions
└── config.py                # Environment setup
```

---

## 4. Implementation Plan

### Phase 1: Extract Shared Utilities
1. Create `receipt_processing/` module
2. Move `setup_environment()` to `config.py`
3. Move `get_image_from_s3()` to `image_utils.py`
4. Create common coordinate conversion utilities

### Phase 2: Modularize Visualization Script
1. Create `receipt_visualization/` module
2. Extract image loading logic
3. Extract coordinate transforms
4. Extract visualization drawing
5. Refactor main function to use modules

### Phase 3: Modularize Split Script
1. Create `receipt_splitting/` module
2. Extract data loading
3. Extract coordinate transforms (biggest win - remove duplication)
4. Extract entity creation (break down 360-line function)
5. Extract image processing
6. Extract persistence logic
7. Refactor main to use modules

### Phase 4: Integration
1. Update imports in both scripts
2. Test with existing use cases
3. Update documentation

---

## 5. Key Benefits

1. **Reusability**: Coordinate transforms can be shared
2. **Testability**: Smaller functions are easier to test
3. **Maintainability**: Clear separation of concerns
4. **Readability**: Main functions become orchestrators, not implementations
5. **DRY**: Remove duplicated coordinate transformation logic

---

## 6. Example: Before/After

### Before (split_receipt.py - create_split_receipt_records)

```python
def create_split_receipt_records(...):  # 360 lines!
    # Filter data
    # Calculate bounds
    # Create image
    # Upload to S3
    # Create Receipt entity
    # Transform and create ReceiptLine (100 lines)
    # Transform and create ReceiptWord (100 lines)
    # Transform and create ReceiptLetter (50 lines)
    return {...}
```

### After (modularized)

```python
# receipt_splitting/entity_creation.py
def create_split_receipt_entities(cluster_data, original_receipt, ...):
    bounds = calculate_receipt_bounds_from_words(...)
    receipt_image = create_and_upload_receipt_image(...)
    receipt = create_receipt_entity(...)

    lines = create_receipt_lines(
        cluster_data.lines,
        original_receipt,
        bounds,
        ...
    )
    words = create_receipt_words(...)
    letters = create_receipt_letters(...)

    return {
        "receipt": receipt,
        "lines": lines,
        "words": words,
        "letters": letters,
    }
```

---

## Next Steps

1. Review this plan
2. Start with Phase 1 (shared utilities)
3. Then Phase 2 (visualization)
4. Finally Phase 3 (split script - most complex)

