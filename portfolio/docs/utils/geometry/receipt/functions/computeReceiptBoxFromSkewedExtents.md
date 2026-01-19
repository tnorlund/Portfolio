[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / computeReceiptBoxFromSkewedExtents

# Function: computeReceiptBoxFromSkewedExtents()

> **computeReceiptBoxFromSkewedExtents**(`hull`, `cx`, `cy`, `rotationDeg`): [`Point`](../../basic/interfaces/Point.md)[] \| `null`

Defined in: [utils/geometry/receipt.ts:16](https://github.com/tnorlund/Portfolio/blob/88e7a17c2c883751304e449941bf16003f15b8f2/portfolio/utils/geometry/receipt.ts#L16)

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
