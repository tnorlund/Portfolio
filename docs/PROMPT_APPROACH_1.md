# Prompt for Approach 1: Hybrid (Simple Top/Bottom + Old Left/Right)

You are working on the Portfolio repo at /Users/tnorlund/portfolio_faster_mac_ocr/Portfolio

## Task
Implement Approach 1 (Hybrid) for fixing PHOTO perspective transform left/right edge detection.

## Setup
1. Checkout the base branch: `git checkout feat/simplify-photo-perspective-transform`
2. Create a new branch: `git checkout -b feat/photo-transform-approach1-hybrid`
3. Activate the venv: `source .venv312/bin/activate`

## Implementation
Read `docs/APPROACH_1_HYBRID.md` for the full approach specification.

Modify `receipt_upload/receipt_upload/receipt_processing/photo.py`:
- Keep the simple top/bottom edge detection (using top/bottom line corners)
- Restore the old angled left/right edge detection using:
  - `find_hull_extremes_along_angle()`
  - `refine_hull_extremes_with_hull_edge_alignment()`
  - `create_boundary_line_from_points()`
- Create top/bottom boundary lines from line corners
- Intersect all four boundaries to get final corners

## Testing
Run these commands to verify the fix:
```bash
python scripts/compare_perspective_transforms.py --stack dev
python scripts/visualize_perspective_transforms.py --stack dev --output-dir viz_approach1
```

Focus on these two problematic images:
- 2c453d65-edae-4aeb-819a-612b27d99894
- 8362b52a-60a1-4534-857f-028dd531976e

The fix is successful if the simplified corners match the stored corners more closely for tilted receipts.

## Deliverable
Commit your changes with a descriptive message and push the branch.
