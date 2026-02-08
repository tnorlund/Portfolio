[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / computeReceiptBoxFromSkewedExtents

# Function: computeReceiptBoxFromSkewedExtents()

> **computeReceiptBoxFromSkewedExtents**(`hull`, `cx`, `cy`, `rotationDeg`): [`Point`](../../basic/interfaces/Point.md)[] \| `null`

Defined in: [utils/geometry/receipt.ts:16](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/utils/geometry/receipt.ts#L16)

Determine a bounding box from a skewed hull by estimating the
vertical extents after de-skewing.

## Parameters

### hull

[`Point`](../../basic/interfaces/Point.md)[]

Convex hull points of the receipt.

### cx

`number`

X‑coordinate of the hull centroid.

### cy

`number`

Y‑coordinate of the hull centroid.

### rotationDeg

`number`

Rotation angle in degrees used to deskew
the hull.

## Returns

[`Point`](../../basic/interfaces/Point.md)[] \| `null`

Four points representing the receipt box in clockwise
order or `null` when the hull is empty.
