[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / theilSen

# Function: theilSen()

> **theilSen**(`pts`): `object`

Defined in: [utils/geometry/basic.ts:228](https://github.com/tnorlund/Portfolio/blob/20823584cb8248febb44ef5943d3c8c6244823d5/portfolio/utils/geometry/basic.ts#L228)

Perform Theil–Sen regression to estimate a line through a set of
points.

## Parameters

### pts

[`Point`](../interfaces/Point.md)[]

Sample points where `x` is the independent variable and
`y` is the dependent variable.

## Returns

`object`

The estimated slope and intercept of the regression line.

### intercept

> **intercept**: `number`

### slope

> **slope**: `number` = `0`
