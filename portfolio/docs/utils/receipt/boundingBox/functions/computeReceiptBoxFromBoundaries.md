[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / computeReceiptBoxFromBoundaries

# Function: computeReceiptBoxFromBoundaries()

> **computeReceiptBoxFromBoundaries**(`topBoundary`, `bottomBoundary`, `leftBoundary`, `rightBoundary`, `fallbackCentroid?`): [`Point`](../../../../types/api/interfaces/Point.md)[]

Defined in: [utils/receipt/boundingBox.ts:541](https://github.com/tnorlund/Portfolio/blob/b44b7c08b021c204de4ac3ec069d29f18a9dccef/portfolio/utils/receipt/boundingBox.ts#L541)

Compute the final receipt bounding box from four boundary lines (Step 9).

This simplified function takes the top, bottom, left, and right boundary lines
and computes their four intersection points to form the final receipt quadrilateral.
This is a cleaner, more focused approach that separates boundary computation
from intersection calculation.

## Parameters

### topBoundary

[`BoundaryLine`](../interfaces/BoundaryLine.md)

Top boundary line of the receipt

### bottomBoundary

[`BoundaryLine`](../interfaces/BoundaryLine.md)

Bottom boundary line of the receipt

### leftBoundary

[`BoundaryLine`](../interfaces/BoundaryLine.md)

Left boundary line of the receipt

### rightBoundary

[`BoundaryLine`](../interfaces/BoundaryLine.md)

Right boundary line of the receipt

### fallbackCentroid?

[`Point`](../../../../types/api/interfaces/Point.md)

Fallback point if intersections fail

## Returns

[`Point`](../../../../types/api/interfaces/Point.md)[]

Four-point quadrilateral representing the final receipt boundary.
