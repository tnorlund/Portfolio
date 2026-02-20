[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / lineIntersection

# Function: lineIntersection()

> **lineIntersection**(`p1`, `d1`, `p2`, `d2`): [`Point`](../interfaces/Point.md) \| `null`

Defined in: [utils/geometry/basic.ts:112](https://github.com/tnorlund/Portfolio/blob/c4d7207ff2b42f477e2d98b3767374ea806361e6/portfolio/utils/geometry/basic.ts#L112)

Find intersection of two lines defined by point + direction.

## Parameters

### p1

[`Point`](../interfaces/Point.md)

Point on first line

### d1

[`Point`](../interfaces/Point.md)

Direction vector of first line

### p2

[`Point`](../interfaces/Point.md)

Point on second line

### d2

[`Point`](../interfaces/Point.md)

Direction vector of second line

## Returns

[`Point`](../interfaces/Point.md) \| `null`

Intersection point, or null if lines are parallel
