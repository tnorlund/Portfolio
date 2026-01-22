[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / TrainingMetricsEpoch

# Interface: TrainingMetricsEpoch

Defined in: [types/api.ts:314](https://github.com/tnorlund/Portfolio/blob/beb1cba3bd781cabe5bde674bdb68d2db0845be3/portfolio/types/api.ts#L314)

## Properties

### epoch

> **epoch**: `number`

Defined in: [types/api.ts:315](https://github.com/tnorlund/Portfolio/blob/beb1cba3bd781cabe5bde674bdb68d2db0845be3/portfolio/types/api.ts#L315)

***

### is\_best

> **is\_best**: `boolean`

Defined in: [types/api.ts:316](https://github.com/tnorlund/Portfolio/blob/beb1cba3bd781cabe5bde674bdb68d2db0845be3/portfolio/types/api.ts#L316)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:317](https://github.com/tnorlund/Portfolio/blob/beb1cba3bd781cabe5bde674bdb68d2db0845be3/portfolio/types/api.ts#L317)

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

Defined in: [types/api.ts:329](https://github.com/tnorlund/Portfolio/blob/beb1cba3bd781cabe5bde674bdb68d2db0845be3/portfolio/types/api.ts#L329)

***

### confusion\_matrix?

> `optional` **confusion\_matrix**: `object`

Defined in: [types/api.ts:325](https://github.com/tnorlund/Portfolio/blob/beb1cba3bd781cabe5bde674bdb68d2db0845be3/portfolio/types/api.ts#L325)

#### labels

> **labels**: `string`[]

#### matrix

> **matrix**: `number`[][]
