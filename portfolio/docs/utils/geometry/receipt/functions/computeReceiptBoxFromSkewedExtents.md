[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / computeReceiptBoxFromSkewedExtents

# Function: computeReceiptBoxFromSkewedExtents()

> **computeReceiptBoxFromSkewedExtents**(`hull`, `cx`, `cy`, `rotationDeg`): [`Point`](../../basic/interfaces/Point.md)[] \| `null`

Defined in: [utils/geometry/receipt.ts:16](https://github.com/tnorlund/Portfolio/blob/05b501292951f15e7644a172f3257750efc7a576/portfolio/utils/geometry/receipt.ts#L16)

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
