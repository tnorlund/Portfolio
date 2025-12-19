[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / findHullExtremesAlongAngle

# Function: findHullExtremesAlongAngle()

> **findHullExtremesAlongAngle**(`hull`, `centroid`, `angleDeg`): `object`

Defined in: [utils/receipt/boundingBox.ts:305](https://github.com/tnorlund/Portfolio/blob/22a7c48fe830faa465efd67ec044527b2eb5c2f8/portfolio/utils/receipt/boundingBox.ts#L305)

Find the extreme points of a convex hull when projected along a specific angle.

This function projects all hull points onto a line oriented at the given angle
and returns the points that fall at the minimum and maximum positions along
that projection. This is useful for finding the boundary points of a rotated
bounding box.

## Parameters

### hull

[`Point`](../../../../types/api/interfaces/Point.md)[]

Convex hull points to analyze.

### centroid

[`Point`](../../../../types/api/interfaces/Point.md)

Reference point for computing relative positions.

### angleDeg

`number`

Projection angle in degrees (0° = horizontal right).

## Returns

`object`

Object containing the leftmost and rightmost points along the projection.

### leftPoint

> **leftPoint**: [`Point`](../../../../types/api/interfaces/Point.md)

### rightPoint

> **rightPoint**: [`Point`](../../../../types/api/interfaces/Point.md)
