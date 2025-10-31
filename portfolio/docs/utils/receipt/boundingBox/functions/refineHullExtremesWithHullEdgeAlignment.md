[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / refineHullExtremesWithHullEdgeAlignment

# Function: refineHullExtremesWithHullEdgeAlignment()

> **refineHullExtremesWithHullEdgeAlignment**(`hull`, `leftExtreme`, `rightExtreme`, `targetAngle`): `object`

Defined in: [utils/receipt/boundingBox.ts:353](https://github.com/tnorlund/Portfolio/blob/ae7a6851a77a671f63bb0f82fc6050304af5543b/portfolio/utils/receipt/boundingBox.ts#L353)

Refine hull extreme points by selecting CW/CCW neighbors using Hull Edge Alignment.

For each extreme point, this function compares the clockwise and counter-clockwise
neighbors to determine which creates a line that best aligns with adjacent hull edges.
This creates boundary lines that "hug" the hull contour more naturally.

## Parameters

### hull

[`Point`](../../../../types/api/interfaces/Point.md)[]

Convex hull points (ordered CCW).

### leftExtreme

[`Point`](../../../../types/api/interfaces/Point.md)

Left extreme point from Step 7.

### rightExtreme

[`Point`](../../../../types/api/interfaces/Point.md)

Right extreme point from Step 7.

### targetAngle

`number`

Target orientation angle in degrees.

## Returns

`object`

Refined extreme segments with optimal CW/CCW neighbors.

### leftSegment

> **leftSegment**: `object`

#### leftSegment.extreme

> **extreme**: [`Point`](../../../../types/api/interfaces/Point.md)

#### leftSegment.optimizedNeighbor

> **optimizedNeighbor**: [`Point`](../../../../types/api/interfaces/Point.md)

### rightSegment

> **rightSegment**: `object`

#### rightSegment.extreme

> **extreme**: [`Point`](../../../../types/api/interfaces/Point.md)

#### rightSegment.optimizedNeighbor

> **optimizedNeighbor**: [`Point`](../../../../types/api/interfaces/Point.md)
