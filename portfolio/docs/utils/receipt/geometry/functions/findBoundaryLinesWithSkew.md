[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/geometry](../README.md) / findBoundaryLinesWithSkew

# Function: findBoundaryLinesWithSkew()

> **findBoundaryLinesWithSkew**(`lines`, `_hull`, `centroid`, `avgAngle`): `object`

Defined in: [utils/receipt/geometry.ts:18](https://github.com/tnorlund/Portfolio/blob/f579d2121e717dd008c9603f0030a077d7187747/portfolio/utils/receipt/geometry.ts#L18)

Find the subset of lines that form the left and right boundaries of a
skewed receipt.

Lines are projected onto the secondary axis to determine which reside
at the extremes. Those boundary lines are returned along with their
average orientation.

## Parameters

### lines

[`Line`](../../../../types/api/interfaces/Line.md)[]

OCR lines from the image.

### \_hull

[`Point`](../../../geometry/basic/interfaces/Point.md)[]

Convex hull of all line points (unused).

### centroid

[`Point`](../../../geometry/basic/interfaces/Point.md)

Centroid of the hull.

### avgAngle

`number`

Average text angle in degrees.

## Returns

`object`

Edge points and boundary angles for the left and right sides.

### leftBoundaryAngle

> **leftBoundaryAngle**: `number`

### leftEdgePoints

> **leftEdgePoints**: [`Point`](../../../geometry/basic/interfaces/Point.md)[]

### rightBoundaryAngle

> **rightBoundaryAngle**: `number`

### rightEdgePoints

> **rightEdgePoints**: [`Point`](../../../geometry/basic/interfaces/Point.md)[]
