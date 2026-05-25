[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / TrainingMetricsEpoch

# Interface: TrainingMetricsEpoch

Defined in: [types/api.ts:338](https://github.com/tnorlund/Portfolio/blob/40d76c6562d1c50c5c5f7ab68e91ceb31d1b52d5/portfolio/types/api.ts#L338)

## Properties

### epoch

> **epoch**: `number`

Defined in: [types/api.ts:339](https://github.com/tnorlund/Portfolio/blob/40d76c6562d1c50c5c5f7ab68e91ceb31d1b52d5/portfolio/types/api.ts#L339)

***

### is\_best

> **is\_best**: `boolean`

Defined in: [types/api.ts:340](https://github.com/tnorlund/Portfolio/blob/40d76c6562d1c50c5c5f7ab68e91ceb31d1b52d5/portfolio/types/api.ts#L340)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:341](https://github.com/tnorlund/Portfolio/blob/40d76c6562d1c50c5c5f7ab68e91ceb31d1b52d5/portfolio/types/api.ts#L341)

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

Defined in: [types/api.ts:353](https://github.com/tnorlund/Portfolio/blob/40d76c6562d1c50c5c5f7ab68e91ceb31d1b52d5/portfolio/types/api.ts#L353)

***

### confusion\_matrix?

> `optional` **confusion\_matrix**: `object`

Defined in: [types/api.ts:349](https://github.com/tnorlund/Portfolio/blob/40d76c6562d1c50c5c5f7ab68e91ceb31d1b52d5/portfolio/types/api.ts#L349)

#### labels

> **labels**: `string`[]

#### matrix

> **matrix**: `number`[][]
