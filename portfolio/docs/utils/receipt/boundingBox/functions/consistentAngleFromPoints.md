[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/receipt/boundingBox](../README.md) / consistentAngleFromPoints

# Function: consistentAngleFromPoints()

> **consistentAngleFromPoints**(`pts`): `null` \| `number`

Defined in: [utils/receipt/boundingBox.ts:224](https://github.com/tnorlund/Portfolio/blob/9f107d06807c9c891278b4d03f0459fa13572ed6/portfolio/utils/receipt/boundingBox.ts#L224)

Compute angle from points ensuring consistent left-to-right direction.
This eliminates angle direction inconsistencies caused by hull point ordering.
For line orientations, angles are normalized to [0°, 90°] where values near 180°
are treated as being close to 0°.

## Parameters

### pts

[`Point`](../../../../types/api/interfaces/Point.md)[]

## Returns

`null` \| `number`
