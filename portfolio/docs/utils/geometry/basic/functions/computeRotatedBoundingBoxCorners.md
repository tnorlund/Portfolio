[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / computeRotatedBoundingBoxCorners

# Function: computeRotatedBoundingBoxCorners()

> **computeRotatedBoundingBoxCorners**(`hull`, `topLineCorners`, `bottomLineCorners`): [`Point`](../interfaces/Point.md)[]

Defined in: [utils/geometry/basic.ts:139](https://github.com/tnorlund/Portfolio/blob/6161c65dfd4385c62017b440b96ce64da5be717c/portfolio/utils/geometry/basic.ts#L139)

Compute receipt corners using the rotated bounding box approach.

This derives left/right edges from the receipt tilt (average of top/bottom
edge angles) and projects hull points onto the perpendicular axis to find
extremes.

## Parameters

### hull

[`Point`](../interfaces/Point.md)[]

Convex hull points of all word corners

### topLineCorners

[`Point`](../interfaces/Point.md)[]

Corners from top line [TL, TR, BL, BR]

### bottomLineCorners

[`Point`](../interfaces/Point.md)[]

Corners from bottom line [TL, TR, BL, BR]

## Returns

[`Point`](../interfaces/Point.md)[]

Receipt corners [top_left, top_right, bottom_right, bottom_left]
