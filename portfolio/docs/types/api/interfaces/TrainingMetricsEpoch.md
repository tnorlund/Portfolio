[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / TrainingMetricsEpoch

# Interface: TrainingMetricsEpoch

Defined in: [types/api.ts:199](https://github.com/tnorlund/Portfolio/blob/640c08c94f822d6827c45fb56e4b96e801d76378/portfolio/types/api.ts#L199)

## Properties

### epoch

> **epoch**: `number`

Defined in: [types/api.ts:200](https://github.com/tnorlund/Portfolio/blob/640c08c94f822d6827c45fb56e4b96e801d76378/portfolio/types/api.ts#L200)

***

### is\_best

> **is\_best**: `boolean`

Defined in: [types/api.ts:201](https://github.com/tnorlund/Portfolio/blob/640c08c94f822d6827c45fb56e4b96e801d76378/portfolio/types/api.ts#L201)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:202](https://github.com/tnorlund/Portfolio/blob/640c08c94f822d6827c45fb56e4b96e801d76378/portfolio/types/api.ts#L202)

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

Defined in: [types/api.ts:214](https://github.com/tnorlund/Portfolio/blob/640c08c94f822d6827c45fb56e4b96e801d76378/portfolio/types/api.ts#L214)

***

### confusion\_matrix?

> `optional` **confusion\_matrix**: `object`

Defined in: [types/api.ts:210](https://github.com/tnorlund/Portfolio/blob/640c08c94f822d6827c45fb56e4b96e801d76378/portfolio/types/api.ts#L210)

#### labels

> **labels**: `string`[]

#### matrix

> **matrix**: `number`[][]
