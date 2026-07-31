[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / circularMeanAngle

# Function: circularMeanAngle()

> **circularMeanAngle**(`angle1`, `angle2`): `number`

Defined in: [utils/geometry/basic.ts:97](https://github.com/tnorlund/Portfolio/blob/ab1d4b98eb81fb9908351b485d6de1e8c6b79aef/portfolio/utils/geometry/basic.ts#L97)

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
