# Prompt for Approach 2: Rotated Bounding Box

You are working on the Portfolio repo at /Users/tnorlund/portfolio_faster_mac_ocr/Portfolio

## Task
Implement Approach 2 (Rotated Bounds) for fixing PHOTO perspective transform left/right edge detection.

## Setup
1. Checkout the base branch: `git checkout feat/simplify-photo-perspective-transform`
2. Create a new branch: `git checkout -b feat/photo-transform-approach2-rotated`
3. Activate the venv: `source .venv312/bin/activate`

## Implementation
Read `docs/APPROACH_2_ROTATED_BOUNDS.md` for the full approach specification.

Modify `receipt_upload/receipt_upload/receipt_processing/photo.py`:
- Keep finding top/bottom lines by Y position
- Compute the angle of top and bottom edges using `math.atan2()`
- Average the angles to get receipt tilt
- Create left/right edge directions perpendicular to the average tilt
- Project hull points onto the perpendicular axis to find left/right extremes
- Use line intersection to compute final corners

The approach uses only the `math` module - no external geometry function dependencies.

## Testing
Run these commands to verify the fix:
```bash
python scripts/compare_perspective_transforms.py --stack dev
python scripts/visualize_perspective_transforms.py --stack dev --output-dir viz_approach2
```

Focus on these two problematic images:
- 2c453d65-edae-4aeb-819a-612b27d99894
- 8362b52a-60a1-4534-857f-028dd531976e

The fix is successful if the simplified corners match the stored corners more closely for tilted receipts.

## Deliverable
Commit your changes with a descriptive message and push the branch.
