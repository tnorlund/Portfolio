[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / TrainingMetricsEpoch

# Interface: TrainingMetricsEpoch

Defined in: [types/api.ts:369](https://github.com/tnorlund/Portfolio/blob/9acc7e86766e350c09d3a71f83cab383e6ed70a5/portfolio/types/api.ts#L369)

## Properties

### epoch

> **epoch**: `number`

Defined in: [types/api.ts:370](https://github.com/tnorlund/Portfolio/blob/9acc7e86766e350c09d3a71f83cab383e6ed70a5/portfolio/types/api.ts#L370)

***

### is\_best

> **is\_best**: `boolean`

Defined in: [types/api.ts:371](https://github.com/tnorlund/Portfolio/blob/9acc7e86766e350c09d3a71f83cab383e6ed70a5/portfolio/types/api.ts#L371)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:372](https://github.com/tnorlund/Portfolio/blob/9acc7e86766e350c09d3a71f83cab383e6ed70a5/portfolio/types/api.ts#L372)

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

Defined in: [types/api.ts:384](https://github.com/tnorlund/Portfolio/blob/9acc7e86766e350c09d3a71f83cab383e6ed70a5/portfolio/types/api.ts#L384)

***

### confusion\_matrix?

> `optional` **confusion\_matrix**: `object`

Defined in: [types/api.ts:380](https://github.com/tnorlund/Portfolio/blob/9acc7e86766e350c09d3a71f83cab383e6ed70a5/portfolio/types/api.ts#L380)

#### labels

> **labels**: `string`[]

#### matrix

> **matrix**: `number`[][]
