[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / lineIntersection

# Function: lineIntersection()

> **lineIntersection**(`p1`, `d1`, `p2`, `d2`): [`Point`](../interfaces/Point.md) \| `null`

Defined in: [utils/geometry/basic.ts:112](https://github.com/tnorlund/Portfolio/blob/e24f49a4f7bacdddb23b9425fe79f4b9bdee5319/portfolio/utils/geometry/basic.ts#L112)

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
