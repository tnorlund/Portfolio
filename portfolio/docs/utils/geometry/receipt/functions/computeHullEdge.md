[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/receipt](../README.md) / computeHullEdge

# Function: computeHullEdge()

> **computeHullEdge**(`hull`, `bins?`, `pick`): \{ `bottom`: [`Point`](../../basic/interfaces/Point.md); `top`: [`Point`](../../basic/interfaces/Point.md); \} \| `null`

Defined in: [utils/geometry/receipt.ts:96](https://github.com/tnorlund/Portfolio/blob/8f45800d5fb03a5a7cb25df263c8e592b9eb60b7/portfolio/utils/geometry/receipt.ts#L96)

Sample points from a convex hull to estimate the left or right edge
as a straight line.

## Parameters

### hull

[`Point`](../../basic/interfaces/Point.md)[]

Polygon points representing the convex hull.

### bins?

`number` = `12`

Number of vertical bins used to sample representative
points.

### pick

`"left"` \| `"right"`

Which side of the hull to estimate: `"left"` or
`"right"`.

## Returns

\{ `bottom`: [`Point`](../../basic/interfaces/Point.md); `top`: [`Point`](../../basic/interfaces/Point.md); \} \| `null`

The line through the sampled points or `null` when not
enough samples are available.
