[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / TrainingMetricsEpoch

# Interface: TrainingMetricsEpoch

Defined in: [types/api.ts:332](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L332)

## Properties

### epoch

> **epoch**: `number`

Defined in: [types/api.ts:333](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L333)

***

### is\_best

> **is\_best**: `boolean`

Defined in: [types/api.ts:334](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L334)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:335](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L335)

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

Defined in: [types/api.ts:347](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L347)

***

### confusion\_matrix?

> `optional` **confusion\_matrix**: `object`

Defined in: [types/api.ts:343](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L343)

#### labels

> **labels**: `string`[]

#### matrix

> **matrix**: `number`[][]
