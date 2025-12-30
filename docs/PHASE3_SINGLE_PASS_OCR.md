# Phase 3: Single-Pass OCR Architecture

## Overview

Phase 3 completes the Mac-AWS round-trip optimization by enabling the Mac to perform **all OCR processing in a single pass**, eliminating the need for multiple downloads and uploads.

## Current Multi-Pass Flow (Before Phase 3)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CURRENT: Multiple Round-Trips                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  FIRST_PASS:                                                            │
│  ┌──────┐  download   ┌──────┐  OCR     ┌──────────┐                   │
│  │ SQS  │ ──────────► │ Mac  │ ───────► │ S3/Dynamo│                   │
│  └──────┘   image     └──────┘  result  └──────────┘                   │
│                                               │                         │
│                                               ▼                         │
│                                        ┌──────────┐                     │
│                                        │  Lambda  │ classify,          │
│                                        │          │ cluster,           │
│                                        └──────────┘ extract            │
│                                               │                         │
│         ┌─────────────────────────────────────┘                        │
│         │ (For PHOTO/SCAN with N receipts)                             │
│         ▼                                                               │
│  REFINEMENT (repeated N times):                                        │
│  ┌──────┐  download   ┌──────┐  OCR     ┌──────────┐                   │
│  │ SQS  │ ──────────► │ Mac  │ ───────► │ S3/Dynamo│                   │
│  └──────┘  warped     └──────┘  result  └──────────┘                   │
│            receipt                                                      │
│                                                                          │
│  Total round-trips for photo with 3 receipts: 4                        │
└─────────────────────────────────────────────────────────────────────────┘
```

## Proposed Single-Pass Flow (Phase 3)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    PHASE 3: Single Round-Trip                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  SINGLE_PASS:                                                           │
│  ┌──────┐  download   ┌─────────────────────────────────────┐          │
│  │ SQS  │ ──────────► │              Mac                     │          │
│  └──────┘   image     │                                      │          │
│                       │  1. Run Vision OCR (full image)      │          │
│                       │  2. Classify: NATIVE/PHOTO/SCAN      │          │
│                       │  3. If NATIVE: done                  │          │
│                       │  4. If PHOTO/SCAN:                   │          │
│                       │     a. Cluster lines (DBSCAN)        │          │
│                       │     b. For each cluster:             │          │
│                       │        - Compute corners             │          │
│                       │        - Apply perspective transform │          │
│                       │        - Run Vision OCR on warped    │          │
│                       │  5. Upload ALL results               │          │
│                       └──────────────────┬──────────────────┘          │
│                                          │                              │
│                                          ▼                              │
│                                   ┌──────────────┐                      │
│                                   │  S3 Bucket   │                      │
│                                   │              │                      │
│                                   │ • metadata   │                      │
│                                   │ • full OCR   │                      │
│                                   │ • N warped   │                      │
│                                   │   images     │                      │
│                                   │ • N receipt  │                      │
│                                   │   OCRs       │                      │
│                                   └──────┬───────┘                      │
│                                          │                              │
│                                          ▼                              │
│                                   ┌──────────────┐                      │
│                                   │    Lambda    │ Lightweight:         │
│                                   │              │ parse & store        │
│                                   └──────────────┘                      │
│                                                                          │
│  Total round-trips for photo with 3 receipts: 1                        │
└─────────────────────────────────────────────────────────────────────────┘
```

## Performance Improvement

| Scenario | Current Round-Trips | Phase 3 Round-Trips | Improvement |
|----------|--------------------|--------------------|-------------|
| NATIVE (1 receipt) | 1 | 1 | Same |
| PHOTO (1 receipt) | 2 | 1 | 50% reduction |
| PHOTO (3 receipts) | 4 | 1 | 75% reduction |
| SCAN (5 receipts) | 6 | 1 | 83% reduction |

## Implementation Components

### 1. New Swift Modules (in `receipt_ocr_swift/`)

```
Sources/ReceiptOCRCore/
├── Classification/
│   └── ImageClassifier.swift       ✓ (Phase 2 - Complete)
├── Clustering/
│   └── LineClusterer.swift         ✓ (Phase 2 - Complete)
├── Geometry/
│   ├── PerspectiveTransform.swift  ○ (Phase 3 - New)
│   ├── AffineTransform.swift       ○ (Phase 3 - New)
│   └── ConvexHull.swift            ○ (Phase 3 - New)
├── Processing/
│   └── SinglePassProcessor.swift   ○ (Phase 3 - New)
└── OCR/
    └── VisionOCREngine.swift       ✓ (Updated in Phase 2)
```

### 2. New Job Type

```swift
// In constants or models
enum OCRJobType: String, Codable {
    case firstPass = "FIRST_PASS"       // Legacy
    case refinement = "REFINEMENT"      // Legacy
    case singlePass = "SINGLE_PASS"     // New
}
```

### 3. S3 Output Structure

```
ocr_results/{image_id}/
├── metadata.json           # Classification, clustering, job info
├── full_image_ocr.json     # OCR of entire image
├── receipts/
│   ├── receipt_1/
│   │   ├── image.jpg       # Warped receipt image
│   │   └── ocr.json        # Receipt-level OCR
│   ├── receipt_2/
│   │   ├── image.jpg
│   │   └── ocr.json
│   └── ...
└── thumbnails/             # Optional: pre-generated thumbnails
    ├── receipt_1_thumb.jpg
    └── ...
```

### 4. Metadata JSON Format

```json
{
  "image_id": "uuid",
  "job_id": "uuid",
  "job_type": "SINGLE_PASS",
  "classification": {
    "image_type": "PHOTO",
    "margins": {...},
    "scan_distance": 0.5,
    "photo_distance": 0.1,
    "image_width": 4032,
    "image_height": 3024
  },
  "clustering": {
    "clusters": {
      "1": [0, 1, 2, 3, 4],
      "2": [5, 6, 7, 8, 9]
    }
  },
  "receipts": [
    {
      "receipt_id": 1,
      "cluster_id": 1,
      "corners": {
        "top_left": [100, 50],
        "top_right": [500, 55],
        "bottom_left": [95, 800],
        "bottom_right": [505, 795]
      },
      "warped_dimensions": {
        "width": 400,
        "height": 750
      },
      "s3_keys": {
        "image": "receipts/receipt_1/image.jpg",
        "ocr": "receipts/receipt_1/ocr.json"
      }
    }
  ],
  "processing_time_ms": 1234,
  "created_at": "2024-01-15T10:30:00Z"
}
```

### 5. Lambda Handler Changes

```python
# In ocr_processor.py

def process_ocr_job(self, image_id: str, job_id: str) -> dict:
    ocr_job = self._get_ocr_job(image_id, job_id)

    if ocr_job.job_type == OCRJobType.SINGLE_PASS:
        return self._process_single_pass_job(ocr_job)
    elif ocr_job.job_type == OCRJobType.FIRST_PASS:
        return self._process_first_pass_job(...)  # Legacy
    elif ocr_job.job_type == OCRJobType.REFINEMENT:
        return self._process_refinement_job(...)  # Legacy

def _process_single_pass_job(self, ocr_job: OCRJob) -> dict:
    """
    Process a SINGLE_PASS job where Mac has already done all OCR.

    This is a lightweight operation:
    1. Download metadata.json from S3
    2. Parse pre-computed classification and clustering
    3. For each receipt:
       a. Download receipt OCR JSON
       b. Parse into Line/Word/Letter entities
       c. Store in DynamoDB
    4. Update OCRRoutingDecision as COMPLETED
    """
    # No image downloads, no OCR, no transforms
    # Just parse and store
```

## Swift Implementation Details

### PerspectiveTransform.swift

```swift
import CoreImage
import Accelerate

public struct PerspectiveTransform {
    /// Apply perspective transform to extract a receipt from an image
    ///
    /// - Parameters:
    ///   - image: Source CGImage
    ///   - corners: Four corner points [TL, TR, BR, BL] in pixel coordinates
    ///   - outputSize: Desired output dimensions
    /// - Returns: Warped CGImage
    public func apply(
        to image: CGImage,
        corners: [CGPoint],
        outputSize: CGSize
    ) -> CGImage? {
        // Use Core Image CIPerspectiveCorrection filter
        // or manual homography matrix calculation
    }

    /// Compute perspective transform coefficients (8 parameters)
    public func computeCoefficients(
        sourceCorners: [CGPoint],
        destinationCorners: [CGPoint]
    ) -> [CGFloat] {
        // Solve 8x8 linear system using Accelerate framework
    }
}
```

### SinglePassProcessor.swift

```swift
public struct SinglePassProcessor {
    private let ocrEngine: VisionOCREngine
    private let classifier: ImageClassifier
    private let clusterer: LineClusterer
    private let transformer: PerspectiveTransform

    public func process(imageURL: URL) throws -> SinglePassResult {
        // 1. Run full-image OCR
        let fullOCR = try ocrEngine.processImage(imageURL)

        // 2. Get image dimensions
        let dimensions = getImageDimensions(imageURL)

        // 3. Classify
        let classification = classifier.classify(
            lines: fullOCR.lines,
            imageWidth: dimensions.width,
            imageHeight: dimensions.height
        )

        // 4. Handle based on type
        switch classification.imageType {
        case .native:
            return SinglePassResult(
                fullOCR: fullOCR,
                classification: classification,
                receipts: [ReceiptResult(id: 1, ocr: fullOCR)]
            )

        case .photo, .scan:
            // 5. Cluster lines
            let clustering = clusterer.cluster(
                lines: fullOCR.lines,
                imageType: classification.imageType
            )

            // 6. Process each cluster
            var receipts: [ReceiptResult] = []
            for (clusterId, lineIndices) in clustering.clusters {
                guard clusterId != -1 else { continue }

                // Get cluster lines
                let clusterLines = lineIndices.map { fullOCR.lines[$0] }

                // Compute corners (Phase 1 simplified approach)
                let corners = computeReceiptCorners(
                    lines: clusterLines,
                    imageSize: dimensions
                )

                // Apply perspective transform
                let warpedImage = transformer.apply(
                    to: image,
                    corners: corners,
                    outputSize: estimateReceiptSize(corners)
                )

                // OCR the warped image
                let receiptOCR = try ocrEngine.processImage(warpedImage)

                receipts.append(ReceiptResult(
                    id: receipts.count + 1,
                    corners: corners,
                    warpedImage: warpedImage,
                    ocr: receiptOCR
                ))
            }

            return SinglePassResult(
                fullOCR: fullOCR,
                classification: classification,
                clustering: clustering,
                receipts: receipts
            )
        }
    }
}
```

## Migration Strategy

### Backwards Compatibility

- Lambda continues to support `FIRST_PASS` and `REFINEMENT` job types
- New Mac versions send `SINGLE_PASS` jobs
- Old Mac versions (pre-Phase 3) continue working with legacy flow
- Gradual rollout via feature flag or version detection

### Rollout Plan

1. **Deploy Lambda changes** with `SINGLE_PASS` handler (dormant)
2. **Deploy Swift changes** to Mac with feature flag disabled
3. **Enable feature flag** for internal testing
4. **Monitor metrics**: processing time, success rate, data quality
5. **Gradual rollout** to all users

## Success Metrics

| Metric | Target |
|--------|--------|
| Average round-trips per PHOTO | < 1.5 (currently ~3) |
| P95 processing time | < 30s (currently ~60s for multi-receipt) |
| OCR accuracy | No regression from current |
| Error rate | < 0.1% |

## Dependencies

- **Phase 1**: Simplified PHOTO perspective transform (PR #585)
- **Phase 2**: Swift classification and clustering (PR #586)
- **Core Image**: macOS 10.4+ (already supported)
- **Accelerate**: macOS 10.0+ (already supported)

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Core Image perspective quality | Test with real receipts, fallback to legacy if needed |
| Memory usage for large images | Stream processing, limit concurrent receipts |
| Swift/Python parity drift | Shared test fixtures, integration tests |
| Network timeout on large uploads | Multipart upload, retry logic |
