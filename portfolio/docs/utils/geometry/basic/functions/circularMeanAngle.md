[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / circularMeanAngle

# Function: circularMeanAngle()

> **circularMeanAngle**(`angle1`, `angle2`): `number`

Defined in: [utils/geometry/basic.ts:97](https://github.com/tnorlund/Portfolio/blob/234c9d0714c71671a1f209bec9b1dc15f6a4995c/portfolio/utils/geometry/basic.ts#L97)

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
