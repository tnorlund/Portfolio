[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / TrainingMetricsEpoch

# Interface: TrainingMetricsEpoch

Defined in: [types/api.ts:307](https://github.com/tnorlund/Portfolio/blob/620fde3d79825b25c4bd94f7d79d06b5b9d79d0f/portfolio/types/api.ts#L307)

## Properties

### epoch

> **epoch**: `number`

Defined in: [types/api.ts:308](https://github.com/tnorlund/Portfolio/blob/620fde3d79825b25c4bd94f7d79d06b5b9d79d0f/portfolio/types/api.ts#L308)

***

### is\_best

> **is\_best**: `boolean`

Defined in: [types/api.ts:309](https://github.com/tnorlund/Portfolio/blob/620fde3d79825b25c4bd94f7d79d06b5b9d79d0f/portfolio/types/api.ts#L309)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:310](https://github.com/tnorlund/Portfolio/blob/620fde3d79825b25c4bd94f7d79d06b5b9d79d0f/portfolio/types/api.ts#L310)

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

Defined in: [types/api.ts:322](https://github.com/tnorlund/Portfolio/blob/620fde3d79825b25c4bd94f7d79d06b5b9d79d0f/portfolio/types/api.ts#L322)

***

### confusion\_matrix?

> `optional` **confusion\_matrix**: `object`

Defined in: [types/api.ts:318](https://github.com/tnorlund/Portfolio/blob/620fde3d79825b25c4bd94f7d79d06b5b9d79d0f/portfolio/types/api.ts#L318)

#### labels

> **labels**: `string`[]

#### matrix

> **matrix**: `number`[][]
