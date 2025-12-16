[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / theilSen

# Function: theilSen()

> **theilSen**(`pts`): `object`

Defined in: [utils/geometry/basic.ts:97](https://github.com/tnorlund/Portfolio/blob/c6c53ea7e2e3cd94d58d276e15daa03dc2914026/portfolio/utils/geometry/basic.ts#L97)

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
