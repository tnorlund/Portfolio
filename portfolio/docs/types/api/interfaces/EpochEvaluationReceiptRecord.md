[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / EpochEvaluationReceiptRecord

# Interface: EpochEvaluationReceiptRecord

Defined in: [types/api.ts:646](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L646)

## Properties

### checkpoint

> **checkpoint**: `string`

Defined in: [types/api.ts:649](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L649)

***

### epoch

> **epoch**: `number` \| `null`

Defined in: [types/api.ts:648](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L648)

***

### inference\_time\_ms

> **inference\_time\_ms**: `number`

Defined in: [types/api.ts:656](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L656)

***

### label\_list

> **label\_list**: `string`[]

Defined in: [types/api.ts:650](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L650)

***

### original

> **original**: `object`

Defined in: [types/api.ts:651](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L651)

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

Defined in: [types/api.ts:647](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L647)
