[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LayoutLMReceiptInference

# Interface: LayoutLMReceiptInference

Defined in: [types/api.ts:424](https://github.com/tnorlund/Portfolio/blob/e6fc8648801217382ea31ef1a80589759d598282/portfolio/types/api.ts#L424)

## Properties

### inference\_time\_ms

> **inference\_time\_ms**: `number`

Defined in: [types/api.ts:459](https://github.com/tnorlund/Portfolio/blob/e6fc8648801217382ea31ef1a80589759d598282/portfolio/types/api.ts#L459)

***

### original

> **original**: `object`

Defined in: [types/api.ts:426](https://github.com/tnorlund/Portfolio/blob/e6fc8648801217382ea31ef1a80589759d598282/portfolio/types/api.ts#L426)

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

Defined in: [types/api.ts:425](https://github.com/tnorlund/Portfolio/blob/e6fc8648801217382ea31ef1a80589759d598282/portfolio/types/api.ts#L425)

***

### cached\_at?

> `optional` **cached\_at**: `string`

Defined in: [types/api.ts:460](https://github.com/tnorlund/Portfolio/blob/e6fc8648801217382ea31ef1a80589759d598282/portfolio/types/api.ts#L460)

***

### entities\_summary?

> `optional` **entities\_summary**: [`LayoutLMEntitiesSummary`](LayoutLMEntitiesSummary.md)

Defined in: [types/api.ts:458](https://github.com/tnorlund/Portfolio/blob/e6fc8648801217382ea31ef1a80589759d598282/portfolio/types/api.ts#L458)

***

### metrics?

> `optional` **metrics**: `object`

Defined in: [types/api.ts:445](https://github.com/tnorlund/Portfolio/blob/e6fc8648801217382ea31ef1a80589759d598282/portfolio/types/api.ts#L445)

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

### model\_info?

> `optional` **model\_info**: `object`

Defined in: [types/api.ts:453](https://github.com/tnorlund/Portfolio/blob/e6fc8648801217382ea31ef1a80589759d598282/portfolio/types/api.ts#L453)

#### device

> **device**: `string`

#### model\_name

> **model\_name**: `string`

#### s3\_uri

> **s3\_uri**: `string`
