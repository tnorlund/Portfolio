[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / TrainingMetricsEpoch

# Interface: TrainingMetricsEpoch

Defined in: [types/api.ts:370](https://github.com/tnorlund/Portfolio/blob/68085483b9a46d4d6bd6bfb8f5536de998bf11da/portfolio/types/api.ts#L370)

## Properties

### epoch

> **epoch**: `number`

Defined in: [types/api.ts:371](https://github.com/tnorlund/Portfolio/blob/68085483b9a46d4d6bd6bfb8f5536de998bf11da/portfolio/types/api.ts#L371)

***

### is\_best

> **is\_best**: `boolean`

Defined in: [types/api.ts:372](https://github.com/tnorlund/Portfolio/blob/68085483b9a46d4d6bd6bfb8f5536de998bf11da/portfolio/types/api.ts#L372)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:373](https://github.com/tnorlund/Portfolio/blob/68085483b9a46d4d6bd6bfb8f5536de998bf11da/portfolio/types/api.ts#L373)

#### val\_f1

> **val\_f1**: `number`

#### eval\_loss?

> `optional` **eval\_loss?**: `number`

#### learning\_rate?

> `optional` **learning\_rate?**: `number`

#### train\_loss?

> `optional` **train\_loss?**: `number`

#### val\_precision?

> `optional` **val\_precision?**: `number`

#### val\_recall?

> `optional` **val\_recall?**: `number`

***

### per\_label

> **per\_label**: `Record`\<`string`, \{ `f1`: `number`; `precision`: `number`; `recall`: `number`; `support`: `number`; \}\>

Defined in: [types/api.ts:385](https://github.com/tnorlund/Portfolio/blob/68085483b9a46d4d6bd6bfb8f5536de998bf11da/portfolio/types/api.ts#L385)

***

### confusion\_matrix?

> `optional` **confusion\_matrix?**: `object`

Defined in: [types/api.ts:381](https://github.com/tnorlund/Portfolio/blob/68085483b9a46d4d6bd6bfb8f5536de998bf11da/portfolio/types/api.ts#L381)

#### labels

> **labels**: `string`[]

#### matrix

> **matrix**: `number`[][]
