[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / circularMeanAngle

# Function: circularMeanAngle()

> **circularMeanAngle**(`angle1`, `angle2`): `number`

Defined in: [utils/geometry/basic.ts:97](https://github.com/tnorlund/Portfolio/blob/b6a4e01be029be6a9f9a6b45e599db3b415227ee/portfolio/utils/geometry/basic.ts#L97)

Circular mean of two angles (handles ±π wraparound).
For example, averaging +179° and -179° gives ±180° instead of 0°.

## Parameters

### angle1

`number`

First angle in radians

### angle2

`number`

Second angle in radians

## Returns

`number`

Mean angle in radians
