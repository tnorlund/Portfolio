[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LayoutLMReceiptInference

# Interface: LayoutLMReceiptInference

Defined in: [types/api.ts:416](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L416)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:450](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L450)

***

### entities\_summary

> **entities\_summary**: [`LayoutLMEntitiesSummary`](LayoutLMEntitiesSummary.md)

Defined in: [types/api.ts:448](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L448)

***

### inference\_time\_ms

> **inference\_time\_ms**: `number`

Defined in: [types/api.ts:449](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L449)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:435](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L435)

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

Defined in: [types/api.ts:443](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L443)

#### device

> **device**: `string`

#### model\_name

> **model\_name**: `string`

#### s3\_uri

> **s3\_uri**: `string`

***

### original

> **original**: `object`

Defined in: [types/api.ts:418](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L418)

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

Defined in: [types/api.ts:417](https://github.com/tnorlund/Portfolio/blob/2a20c7a2c9e66d447ac56a13220f53123304ed08/portfolio/types/api.ts#L417)
