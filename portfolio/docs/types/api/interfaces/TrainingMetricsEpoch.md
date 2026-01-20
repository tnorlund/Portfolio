[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / TrainingMetricsEpoch

# Interface: TrainingMetricsEpoch

Defined in: [types/api.ts:312](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L312)

## Properties

### epoch

> **epoch**: `number`

Defined in: [types/api.ts:313](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L313)

***

### is\_best

> **is\_best**: `boolean`

Defined in: [types/api.ts:314](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L314)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:315](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L315)

#### val\_f1

> **val\_f1**: `number`

#### eval\_loss?

> `optional` **eval\_loss**: `number`

#### learning\_rate?

> `optional` **learning\_rate**: `number`

#### train\_loss?

> `optional` **train\_loss**: `number`

#### val\_precision?

> `optional` **val\_precision**: `number`

#### val\_recall?

> `optional` **val\_recall**: `number`

***

### per\_label

> **per\_label**: `Record`\<`string`, \{ `f1`: `number`; `precision`: `number`; `recall`: `number`; `support`: `number`; \}\>

Defined in: [types/api.ts:327](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L327)

***

### confusion\_matrix?

> `optional` **confusion\_matrix**: `object`

Defined in: [types/api.ts:323](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L323)

#### labels

> **labels**: `string`[]

#### matrix

> **matrix**: `number`[][]
