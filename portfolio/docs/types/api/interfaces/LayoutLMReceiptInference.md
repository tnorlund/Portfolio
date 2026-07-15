[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LayoutLMReceiptInference

# Interface: LayoutLMReceiptInference

Defined in: [types/api.ts:455](https://github.com/tnorlund/Portfolio/blob/00397190a47ccb322b90f8881ee969ba60752d39/portfolio/types/api.ts#L455)

## Properties

### inference\_time\_ms

> **inference\_time\_ms**: `number`

Defined in: [types/api.ts:490](https://github.com/tnorlund/Portfolio/blob/00397190a47ccb322b90f8881ee969ba60752d39/portfolio/types/api.ts#L490)

***

### original

> **original**: `object`

Defined in: [types/api.ts:457](https://github.com/tnorlund/Portfolio/blob/00397190a47ccb322b90f8881ee969ba60752d39/portfolio/types/api.ts#L457)

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

Defined in: [types/api.ts:456](https://github.com/tnorlund/Portfolio/blob/00397190a47ccb322b90f8881ee969ba60752d39/portfolio/types/api.ts#L456)

***

### cached\_at?

> `optional` **cached\_at**: `string`

Defined in: [types/api.ts:491](https://github.com/tnorlund/Portfolio/blob/00397190a47ccb322b90f8881ee969ba60752d39/portfolio/types/api.ts#L491)

***

### entities\_summary?

> `optional` **entities\_summary**: [`LayoutLMEntitiesSummary`](LayoutLMEntitiesSummary.md)

Defined in: [types/api.ts:489](https://github.com/tnorlund/Portfolio/blob/00397190a47ccb322b90f8881ee969ba60752d39/portfolio/types/api.ts#L489)

***

### metrics?

> `optional` **metrics**: `object`

Defined in: [types/api.ts:476](https://github.com/tnorlund/Portfolio/blob/00397190a47ccb322b90f8881ee969ba60752d39/portfolio/types/api.ts#L476)

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

Defined in: [types/api.ts:484](https://github.com/tnorlund/Portfolio/blob/00397190a47ccb322b90f8881ee969ba60752d39/portfolio/types/api.ts#L484)

#### device

> **device**: `string`

#### model\_name

> **model\_name**: `string`

#### s3\_uri

> **s3\_uri**: `string`
