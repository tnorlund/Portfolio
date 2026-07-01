[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / computeRotatedBoundingBoxCorners

# Function: computeRotatedBoundingBoxCorners()

> **computeRotatedBoundingBoxCorners**(`hull`, `topLineCorners`, `bottomLineCorners`): [`Point`](../interfaces/Point.md)[]

Defined in: [utils/geometry/basic.ts:139](https://github.com/tnorlund/Portfolio/blob/ff8f10919f16aaac38c5d58473271def24ebb7d1/portfolio/utils/geometry/basic.ts#L139)

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
