[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / computeReceiptBoxFromHull

# Function: computeReceiptBoxFromHull()

> **computeReceiptBoxFromHull**(`hull`, `centroid`, `avgAngle`): [`Point`](../../../../types/api/interfaces/Point.md)[]

Defined in: [utils/receipt/boundingBox.ts:83](https://github.com/tnorlund/Portfolio/blob/04c3ab815970fb94dfaddc4b2c0bbf97d818a7be/portfolio/utils/receipt/boundingBox.ts#L83)

Compute a bounding box that best fits a skewed receipt hull.

The hull is rotated so the receipt is axis aligned. After finding the
minimum rectangle in that orientation, the corners are rotated back to
the original space.

## Parameters

### hull

[`Point`](../../../../types/api/interfaces/Point.md)[]

Convex hull of receipt points.

### centroid

[`Point`](../../../../types/api/interfaces/Point.md)

Centroid of the hull.

### avgAngle

`number`

Average text angle in degrees used to deskew the hull.

## Returns

[`Point`](../../../../types/api/interfaces/Point.md)[]

Polygon describing the receipt in clockwise order.
