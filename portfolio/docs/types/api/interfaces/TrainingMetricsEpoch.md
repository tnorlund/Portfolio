[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / TrainingMetricsEpoch

# Interface: TrainingMetricsEpoch

Defined in: [types/api.ts:223](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L223)

## Properties

### epoch

> **epoch**: `number`

Defined in: [types/api.ts:224](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L224)

***

### is\_best

> **is\_best**: `boolean`

Defined in: [types/api.ts:225](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L225)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:226](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L226)

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

Defined in: [types/api.ts:238](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L238)

***

### confusion\_matrix?

> `optional` **confusion\_matrix**: `object`

Defined in: [types/api.ts:234](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L234)

#### labels

> **labels**: `string`[]

#### matrix

> **matrix**: `number`[][]
