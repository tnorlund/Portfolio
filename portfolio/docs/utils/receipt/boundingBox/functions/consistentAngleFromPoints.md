[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / consistentAngleFromPoints

# Function: consistentAngleFromPoints()

> **consistentAngleFromPoints**(`pts`): `number` \| `null`

Defined in: [utils/receipt/boundingBox.ts:224](https://github.com/tnorlund/Portfolio/blob/047ceb8158cff890c2e7d316f24605d5fcb4352e/portfolio/utils/receipt/boundingBox.ts#L224)

Compute angle from points ensuring consistent left-to-right direction.
This eliminates angle direction inconsistencies caused by hull point ordering.
For line orientations, angles are normalized to [0°, 90°] where values near 180°
are treated as being close to 0°.

## Parameters

### pts

[`Point`](../../../../types/api/interfaces/Point.md)[]

## Returns

`number` \| `null`
