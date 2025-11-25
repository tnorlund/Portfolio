[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/geometry/basic](../README.md) / theilSen

# Function: theilSen()

> **theilSen**(`pts`): `object`

Defined in: [utils/geometry/basic.ts:97](https://github.com/tnorlund/Portfolio/blob/ec14721ddbaed57935125ac7244e977aad1c2e30/portfolio/utils/geometry/basic.ts#L97)

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
