[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / findLineEdgesAtSecondaryExtremes

# Function: findLineEdgesAtSecondaryExtremes()

> **findLineEdgesAtSecondaryExtremes**(`lines`, `hull`, `centroid`, `avgAngle`): `object`

Defined in: [utils/geometry/receipt.ts:173](https://github.com/tnorlund/Portfolio/blob/ea7ad8c008eb77694351c5b6c5df00e4b3ef3db6/portfolio/utils/geometry/receipt.ts#L173)

Locate points along the top and bottom edges of the text lines at the
extreme secondary-axis positions.

## Parameters

### lines

[`Line`](../../../../types/api/interfaces/Line.md)[]

OCR lines used to derive the edges.

### hull

[`Point`](../../basic/interfaces/Point.md)[]

Convex hull of all line points.

### centroid

[`Point`](../../basic/interfaces/Point.md)

Centroid of the hull.

### avgAngle

`number`

Average rotation angle of the text lines in
degrees.

## Returns

`object`

Arrays of points describing the top and bottom edges.

### bottomEdge

> **bottomEdge**: [`Point`](../../basic/interfaces/Point.md)[]

### topEdge

> **topEdge**: [`Point`](../../basic/interfaces/Point.md)[]
