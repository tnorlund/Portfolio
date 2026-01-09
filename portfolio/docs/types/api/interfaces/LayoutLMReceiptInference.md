[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LayoutLMReceiptInference

# Interface: LayoutLMReceiptInference

Defined in: [types/api.ts:307](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L307)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:341](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L341)

***

### entities\_summary

> **entities\_summary**: [`LayoutLMEntitiesSummary`](LayoutLMEntitiesSummary.md)

Defined in: [types/api.ts:339](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L339)

***

### inference\_time\_ms

> **inference\_time\_ms**: `number`

Defined in: [types/api.ts:340](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L340)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:326](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L326)

#### correct\_predictions

> **correct\_predictions**: `number`

#### overall\_accuracy

> **overall\_accuracy**: `number`

#### total\_words

> **total\_words**: `number`

#### per\_label\_f1?

> `optional` **per\_label\_f1**: `Record`\<`string`, `number`\>

#### per\_label\_precision?

> `optional` **per\_label\_precision**: `Record`\<`string`, `number`\>

#### per\_label\_recall?

> `optional` **per\_label\_recall**: `Record`\<`string`, `number`\>

***

### model\_info

> **model\_info**: `object`

Defined in: [types/api.ts:334](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L334)

#### device

> **device**: `string`

#### model\_name

> **model\_name**: `string`

#### s3\_uri

> **s3\_uri**: `string`

***

### original

> **original**: `object`

Defined in: [types/api.ts:309](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L309)

#### predictions

> **predictions**: [`LayoutLMPrediction`](LayoutLMPrediction.md)[]

#### receipt

> **receipt**: `object`

##### receipt.cdn\_s3\_bucket

> **cdn\_s3\_bucket**: `string`

##### receipt.cdn\_s3\_key

> **cdn\_s3\_key**: `string`

##### receipt.height

> **height**: `number`

##### receipt.image\_id

> **image\_id**: `string`

##### receipt.receipt\_id

> **receipt\_id**: `number`

##### receipt.width

> **width**: `number`

##### receipt.cdn\_avif\_s3\_key?

> `optional` **cdn\_avif\_s3\_key**: `string`

##### receipt.cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key**: `string`

##### receipt.cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key**: `string`

##### receipt.cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key**: `string`

##### receipt.cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key**: `string`

#### words

> **words**: [`LayoutLMReceiptWord`](LayoutLMReceiptWord.md)[]

***

### receipt\_id

> **receipt\_id**: `string`

Defined in: [types/api.ts:308](https://github.com/tnorlund/Portfolio/blob/60868e98d5ed70d664b8f228fce49eec194acd0a/portfolio/types/api.ts#L308)
