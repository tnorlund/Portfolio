[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / theilSen

# Function: theilSen()

> **theilSen**(`pts`): `object`

Defined in: [utils/geometry/basic.ts:97](https://github.com/tnorlund/Portfolio/blob/6a8b9537b9c2663f3253614621068aa1db55d2d8/portfolio/utils/geometry/basic.ts#L97)

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
