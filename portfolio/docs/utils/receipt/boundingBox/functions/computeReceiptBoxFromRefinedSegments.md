[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / computeReceiptBoxFromRefinedSegments

# Function: computeReceiptBoxFromRefinedSegments()

> **computeReceiptBoxFromRefinedSegments**(`lines`, `hull`, `centroid`, `finalAngle`, `refinedSegments`): [`Point`](../../../../types/api/interfaces/Point.md)[]

Defined in: [utils/receipt/boundingBox.ts:751](https://github.com/tnorlund/Portfolio/blob/079954855eb0c9aa207d7e2a2da01eb969c79c75/portfolio/utils/receipt/boundingBox.ts#L751)

Compute the final receipt bounding box from refined hull edge alignment segments (Step 9).

This function takes the optimal left and right boundary segments determined by Hull Edge
Alignment (step 8) and intersects them with the top and bottom edges to create the final
receipt quadrilateral. This now delegates to the simpler computeReceiptBoxFromBoundaries.

## Parameters

### lines

[`Line`](../../../../types/api/interfaces/Line.md)[]

OCR lines detected on the receipt image.

### hull

[`Point`](../../../../types/api/interfaces/Point.md)[]

Convex hull points of the receipt.

### centroid

[`Point`](../../../../types/api/interfaces/Point.md)

Centroid of the receipt hull.

### finalAngle

`number`

Final refined angle from Step 6.

### refinedSegments

Refined boundary segments from Hull Edge Alignment (step 8).

#### leftSegment

\{ `extreme`: [`Point`](../../../../types/api/interfaces/Point.md); `optimizedNeighbor`: [`Point`](../../../../types/api/interfaces/Point.md); \}

#### leftSegment.extreme

[`Point`](../../../../types/api/interfaces/Point.md)

#### leftSegment.optimizedNeighbor

[`Point`](../../../../types/api/interfaces/Point.md)

#### rightSegment

\{ `extreme`: [`Point`](../../../../types/api/interfaces/Point.md); `optimizedNeighbor`: [`Point`](../../../../types/api/interfaces/Point.md); \}

#### rightSegment.extreme

[`Point`](../../../../types/api/interfaces/Point.md)

#### rightSegment.optimizedNeighbor

[`Point`](../../../../types/api/interfaces/Point.md)

## Returns

[`Point`](../../../../types/api/interfaces/Point.md)[]

Four-point quadrilateral representing the final receipt boundary.
