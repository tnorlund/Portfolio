[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / computeReceiptBoxFromLineEdges

# Function: computeReceiptBoxFromLineEdges()

> **computeReceiptBoxFromLineEdges**(`lines`, `hull`, `centroid`, `avgAngle`): [`Point`](../../basic/interfaces/Point.md)[]

Defined in: [utils/geometry/receipt.ts:274](https://github.com/tnorlund/Portfolio/blob/7f45d68f4d7e3af5e9c876e545da3f2d8ea8d3fe/portfolio/utils/geometry/receipt.ts#L274)

Build a four‑point bounding box for a receipt based on estimated
line edges.

## Parameters

### lines

[`Line`](../../../../types/api/interfaces/Line.md)[]

OCR lines from which the edges are derived.

### hull

[`Point`](../../basic/interfaces/Point.md)[]

Convex hull of all line corners.

### centroid

[`Point`](../../basic/interfaces/Point.md)

Centroid of the hull.

### avgAngle

`number`

Average orientation of the lines in degrees.

## Returns

[`Point`](../../basic/interfaces/Point.md)[]

The receipt polygon defined in clockwise order. Returns an
empty array when no lines are supplied.
