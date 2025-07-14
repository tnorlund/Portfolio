[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / findHullExtentsRelativeToCentroid

# Function: findHullExtentsRelativeToCentroid()

> **findHullExtentsRelativeToCentroid**(`hull`, `centroid`): `object`

Defined in: [utils/receipt/boundingBox.ts:15](https://github.com/tnorlund/Portfolio/blob/9c3b6b445df61d0f11eac4aa1a6221384f96b729/portfolio/utils/receipt/boundingBox.ts#L15)

Get the extreme coordinates of a convex hull relative to its centroid.

The hull is translated so that the centroid is at the origin. The
returned values include both the minimum and maximum offsets as well
as the corresponding hull points.

## Parameters

### hull

[`Point`](../../../../types/api/interfaces/Point.md)[]

Convex hull points of the receipt.

### centroid

[`Point`](../../../../types/api/interfaces/Point.md)

Centroid of the hull used for translation.

## Returns

`object`

### bottomPoint

> **bottomPoint**: [`Point`](../../../../types/api/interfaces/Point.md)

### leftPoint

> **leftPoint**: [`Point`](../../../../types/api/interfaces/Point.md)

### maxX

> **maxX**: `number`

### maxY

> **maxY**: `number`

### minX

> **minX**: `number`

### minY

> **minY**: `number`

### rightPoint

> **rightPoint**: [`Point`](../../../../types/api/interfaces/Point.md)

### topPoint

> **topPoint**: [`Point`](../../../../types/api/interfaces/Point.md)
