# Handoff: Swift re-OCR strategies + tilted-quad geometry

## 1. THE UNFINISHED SWIFT WORK — salvageable, ~90% done, UNCOMMITTED

Worktree `/Users/tnorlund/Portfolio/.claude/worktrees/agent-a8c5ed403a0d5bcd6` (branch `feat/reocr-strategies`) exists but has **zero commits ahead of origin/main**. All work is uncommitted-but-intact. **Do not delete this worktree** — recover with `git -C <wt> stash` / commit, or copy the 4 files.

- `Sources/ReceiptOCRCore/OCR/ReOCRPreprocessor.swift` (new, 147 lines) — `plain`/`invert` (CIColorInvert) / `deskew` (Vision `.fast` pass → weighted-median baseline angle → rotate, composited over white) / `upscale2x` (CILanczos). Best-effort: every failure returns the original image. No new package deps.
- `Sources/ReceiptOCRCore/Models/OCRJob.swift` (modified) — adds `ReOCRStrategy` enum + `reocrStrategy`/`reocrMechanism` fields, DynamoDB to/from-item mapping, absent/unrecognized → `.plain`.
- `Sources/ReceiptOCRCore/Worker/OCRWorker.swift` (modified) — `cropImageData(_:region:strategy:)` at ~L207 applies the preprocess after `cgImage.cropping` and before PNG encode (~L229-235); regional branch at ~L294-310 passes `job.reocrStrategy`; result JSON annotated with `reocr_strategy_applied` at ~L454.
- 3 new test files (`ReOCRPreprocessorTests`, `ReOCRTestImages`, `ReOCRWorkerStrategyTests`, 490 lines).

**Contract is already MERGED on origin/main and matches the Swift code exactly:**
`VALID_REOCR_STRATEGIES = ("plain", "invert", "deskew", "upscale2x")` in `receipt_dynamo/receipt_dynamo/entities/ocr_job.py:22`; fields `reocr_strategy` (L57) and `reocr_mechanism` (L58), serialized as `{"S": ...}` / `{"NULL": true}` (L233-241), parsed at L419. Ladder logic in `receipt_upload/receipt_upload/line_items/reocr_strategy.py` (`mechanism_key`, `ladder`, `choose_strategy`, `build_ledger`) with the outcome ledger at `line_items/assets/reocr_ladder.json`. Writers already live: `infra/trigger_reocr_lambda/lambdas/trigger_reocr.py:89,122,157` and `infra/receipt_line_item_updater/line_item_processor.py:331,355`.

**What remains:** commit the 4 files, build, run tests, PR. Python writes the field today and Swift ignores it — that's the whole gap.

## 2. BUILD REALITY

Cold builds are slow because `Package.swift:15` pulls `soto` 6.7+ (SotoS3 + SotoSQS + SotoDynamoDB → NIO/swift-crypto transitive tree); `.build` is ~1.9 GB.
- **WARM build exists** at `/Users/tnorlund/Portfolio/.claude/worktrees/backfill-main/receipt_ocr_swift/.build` (release binary `arm64-apple-macosx/release/receipt-ocr`, 37 MB, built 2026-08-03 16:35).
- The agent worktree **also already has a warm 2.0 GB `.build`** — build there, don't start fresh.
- **CLT-vs-Xcode:** this Mac is `xcode-select -p → /Library/Developer/CommandLineTools`, Swift 6.3.3. CLT has no XCTest, so tests must use **swift-testing** (`#expect`, as the existing suites do). The mini has full Xcode.

## 3. TILTED-QUAD FINDING — root cause is NOT the overlay; it's hardcoded in Swift

Dev receipt `492f9ae1-90a5-4943-9f26-8bb98052fb4d` r1: 64 words, all `angle_degrees` 0 or ~1e-14, and the word **quads themselves** are axis-aligned (`top_right.y − top_left.y ≈ 1e-16`). Receipt corners are exactly axis-aligned (`top_left.y == top_right.y = 0.76842`, both left x = 0.23086).

I traced every Python step and **they all preserve tilt**: `_apply_region_mapping` (ocr_processor.py:496) maps all four corners individually, not just the bbox; `_get_perspective_coeffs` (L272) on an axis-aligned quad is a pure scale/translate; `inverse_perspective_transform` (L818-838) is tilt-preserving; the word writer copies `new_word.angle_degrees` verbatim (L1171).

**The real cause is `receipt_ocr_swift/Sources/ReceiptOCRCore/OCR/VisionOCREngine.swift`:**
- `angleDegrees: 0.0` is **literally hardcoded** at **L442 (letters), L460 (words), L478 (lines)**.
- `cornerPoints(from rect: CGRect)` at **L264-271** synthesizes all four corners from an **axis-aligned CGRect** (`minX/maxX/minY/maxY`), used at L416, L433, L470. Vision's real quad corners (`obs.topLeft`… ) are only used for **barcodes** (L299-302), never for text.

So the worker has *never* emitted tilt, for any job type. This cascades: the receipt box is derived from OCR line corners (`geometry/edge_detection.py: compute_edge`, `compute_final_receipt_tilt`), so axis-aligned inputs can only produce an axis-aligned receipt quad — which is why this receipt's quad is axis-aligned in the first place. The re-OCR isn't losing tilt; tilt was never measured.

**Fix:** (a) in `VisionOCREngine.swift`, use `obs.topLeft/topRight/bottomLeft/bottomRight` for text as barcodes already do, and compute `angleDegrees = atan2(dy, dx)` from the baseline instead of 0.0. That alone restores tilt end-to-end. (b) Only for already-ingested receipts does the quad need re-deriving from raw pixels — `geometry/edge_detection.py` (`compute_final_receipt_tilt`, `compute_receipt_box_from_boundaries`, `create_boundary_line_from_theil_sen`) and `geometry/receipt_box.py` (`compute_receipt_box_from_skewed_extents`, `find_hull_extents_relative_to_centroid`) already implement tilt-aware box construction — but they consume OCR line geometry, so they inherit the same zeroed input. True paper-edge detection from pixels does **not** exist yet and would be new work. Note (a) changes first-pass geometry for all future uploads — regenerate `Fixtures/swift_single_pass_contract.json` and expect golden churn.

## 4. OTHER KNOWN-BROKEN

- **Swift decoder parity frozen pre-#1320/#1321.** `Tests/ReceiptOCRCoreTests/LineItemParityTests.swift` gates on `Fixtures/line_items_parity_expected.json` (33 receipts), generated before `a0d2f53d4` (#1320 non-product band filter) and `e68f39089` (#1321 printed grand-total fallback). The Swift port in worktree `agent-a9e52e006ef1dc96c` (`feat/swift-line-item-decoder`, `LineItems/LineItemDecoder.swift`) has neither, so parity passes against a stale Python. Also missing #1349 (tender rows never anchor printed totals). Regenerate expected JSON from current Python before trusting the gate.
- **Dev OCR fixture drift (Costco/Target)** still blocks full golden fixture regeneration — `line_items_golden_ocr.json` is a byte copy of `receipt_upload/tests/fixtures/line_items_golden_ocr.json`, so both sides must be regenerated together.
