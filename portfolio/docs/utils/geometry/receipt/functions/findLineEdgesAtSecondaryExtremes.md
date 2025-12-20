[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / findLineEdgesAtSecondaryExtremes

# Function: findLineEdgesAtSecondaryExtremes()

> **findLineEdgesAtSecondaryExtremes**(`lines`, `hull`, `centroid`, `avgAngle`): `object`

Defined in: [utils/geometry/receipt.ts:173](https://github.com/tnorlund/Portfolio/blob/00e6629dc94d154a45135de2d9455e8f8b98b514/portfolio/utils/geometry/receipt.ts#L173)

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
