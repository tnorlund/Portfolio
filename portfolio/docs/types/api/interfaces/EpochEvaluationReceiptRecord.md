[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / EpochEvaluationReceiptRecord

# Interface: EpochEvaluationReceiptRecord

Defined in: [types/api.ts:575](https://github.com/tnorlund/Portfolio/blob/86f66d450e8f23885c29a874b8ff38d0d6f5e558/portfolio/types/api.ts#L575)

## Properties

### checkpoint

> **checkpoint**: `string`

Defined in: [types/api.ts:578](https://github.com/tnorlund/Portfolio/blob/86f66d450e8f23885c29a874b8ff38d0d6f5e558/portfolio/types/api.ts#L578)

***

### epoch

> **epoch**: `number` \| `null`

Defined in: [types/api.ts:577](https://github.com/tnorlund/Portfolio/blob/86f66d450e8f23885c29a874b8ff38d0d6f5e558/portfolio/types/api.ts#L577)

***

### inference\_time\_ms

> **inference\_time\_ms**: `number`

Defined in: [types/api.ts:585](https://github.com/tnorlund/Portfolio/blob/86f66d450e8f23885c29a874b8ff38d0d6f5e558/portfolio/types/api.ts#L585)

***

### label\_list

> **label\_list**: `string`[]

Defined in: [types/api.ts:579](https://github.com/tnorlund/Portfolio/blob/86f66d450e8f23885c29a874b8ff38d0d6f5e558/portfolio/types/api.ts#L579)

***

### original

> **original**: `object`

Defined in: [types/api.ts:580](https://github.com/tnorlund/Portfolio/blob/86f66d450e8f23885c29a874b8ff38d0d6f5e558/portfolio/types/api.ts#L580)

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

> `optional` **cdn\_avif\_s3\_key?**: `string`

##### receipt.cdn\_medium\_avif\_s3\_key?

> `optional` **cdn\_medium\_avif\_s3\_key?**: `string`

##### receipt.cdn\_medium\_s3\_key?

> `optional` **cdn\_medium\_s3\_key?**: `string`

##### receipt.cdn\_medium\_webp\_s3\_key?

> `optional` **cdn\_medium\_webp\_s3\_key?**: `string`

##### receipt.cdn\_webp\_s3\_key?

> `optional` **cdn\_webp\_s3\_key?**: `string`

#### words

> **words**: [`LayoutLMReceiptWord`](LayoutLMReceiptWord.md)[]

***

### receipt\_id

> **receipt\_id**: `string`

Defined in: [types/api.ts:576](https://github.com/tnorlund/Portfolio/blob/86f66d450e8f23885c29a874b8ff38d0d6f5e558/portfolio/types/api.ts#L576)
