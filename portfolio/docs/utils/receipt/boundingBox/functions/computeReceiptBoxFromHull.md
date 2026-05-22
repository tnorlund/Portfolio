[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / computeReceiptBoxFromHull

# Function: computeReceiptBoxFromHull()

> **computeReceiptBoxFromHull**(`hull`, `centroid`, `avgAngle`): [`Point`](../../../../types/api/interfaces/Point.md)[]

Defined in: [utils/receipt/boundingBox.ts:83](https://github.com/tnorlund/Portfolio/blob/92fb9e1a882d0db4e9f1fc97e19f7e144ef0eaab/portfolio/utils/receipt/boundingBox.ts#L83)

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
