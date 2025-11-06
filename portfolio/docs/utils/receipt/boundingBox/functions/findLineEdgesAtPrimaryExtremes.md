[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / findLineEdgesAtPrimaryExtremes

# Function: findLineEdgesAtPrimaryExtremes()

> **findLineEdgesAtPrimaryExtremes**(`lines`, `_hull`, `centroid`, `avgAngle`): `object`

Defined in: [utils/receipt/boundingBox.ts:143](https://github.com/tnorlund/Portfolio/blob/e1517e856eea6f86d389719a08ff61f208d93fd0/portfolio/utils/receipt/boundingBox.ts#L143)

Gather points along the left and right edges of text lines that sit at
the outermost positions along the primary axis.

This is used when the receipt is skewed: lines are projected onto the
secondary axis to determine which belong to the extreme left and right
boundaries.

## Parameters

### lines

[`Line`](../../../../types/api/interfaces/Line.md)[]

OCR lines detected on the receipt image.

### \_hull

[`Point`](../../../../types/api/interfaces/Point.md)[]

Unused hull points of the receipt.

### centroid

[`Point`](../../../../types/api/interfaces/Point.md)

Centroid of the receipt hull.

### avgAngle

`number`

Average text angle in degrees.

## Returns

`object`

Arrays of points approximating the left and right edges.

### leftEdge

> **leftEdge**: [`Point`](../../../../types/api/interfaces/Point.md)[]

### rightEdge

> **rightEdge**: [`Point`](../../../../types/api/interfaces/Point.md)[]
