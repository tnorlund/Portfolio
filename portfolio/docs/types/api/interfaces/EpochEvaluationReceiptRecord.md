[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / EpochEvaluationReceiptRecord

# Interface: EpochEvaluationReceiptRecord

Defined in: [types/api.ts:543](https://github.com/tnorlund/Portfolio/blob/1aa89ff6d8e3fef601595e7dd218aa2af2e74749/portfolio/types/api.ts#L543)

## Properties

### checkpoint

> **checkpoint**: `string`

Defined in: [types/api.ts:546](https://github.com/tnorlund/Portfolio/blob/1aa89ff6d8e3fef601595e7dd218aa2af2e74749/portfolio/types/api.ts#L546)

***

### epoch

> **epoch**: `number` \| `null`

Defined in: [types/api.ts:545](https://github.com/tnorlund/Portfolio/blob/1aa89ff6d8e3fef601595e7dd218aa2af2e74749/portfolio/types/api.ts#L545)

***

### inference\_time\_ms

> **inference\_time\_ms**: `number`

Defined in: [types/api.ts:553](https://github.com/tnorlund/Portfolio/blob/1aa89ff6d8e3fef601595e7dd218aa2af2e74749/portfolio/types/api.ts#L553)

***

### label\_list

> **label\_list**: `string`[]

Defined in: [types/api.ts:547](https://github.com/tnorlund/Portfolio/blob/1aa89ff6d8e3fef601595e7dd218aa2af2e74749/portfolio/types/api.ts#L547)

***

### original

> **original**: `object`

Defined in: [types/api.ts:548](https://github.com/tnorlund/Portfolio/blob/1aa89ff6d8e3fef601595e7dd218aa2af2e74749/portfolio/types/api.ts#L548)

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

Defined in: [types/api.ts:544](https://github.com/tnorlund/Portfolio/blob/1aa89ff6d8e3fef601595e7dd218aa2af2e74749/portfolio/types/api.ts#L544)
