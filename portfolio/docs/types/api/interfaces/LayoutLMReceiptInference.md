[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LayoutLMReceiptInference

# Interface: LayoutLMReceiptInference

Defined in: [types/api.ts:391](https://github.com/tnorlund/Portfolio/blob/0e5fdf394042873dbd99e64688962f88fbfb97d1/portfolio/types/api.ts#L391)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:425](https://github.com/tnorlund/Portfolio/blob/0e5fdf394042873dbd99e64688962f88fbfb97d1/portfolio/types/api.ts#L425)

***

### entities\_summary

> **entities\_summary**: [`LayoutLMEntitiesSummary`](LayoutLMEntitiesSummary.md)

Defined in: [types/api.ts:423](https://github.com/tnorlund/Portfolio/blob/0e5fdf394042873dbd99e64688962f88fbfb97d1/portfolio/types/api.ts#L423)

***

### inference\_time\_ms

> **inference\_time\_ms**: `number`

Defined in: [types/api.ts:424](https://github.com/tnorlund/Portfolio/blob/0e5fdf394042873dbd99e64688962f88fbfb97d1/portfolio/types/api.ts#L424)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:410](https://github.com/tnorlund/Portfolio/blob/0e5fdf394042873dbd99e64688962f88fbfb97d1/portfolio/types/api.ts#L410)

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

Defined in: [types/api.ts:418](https://github.com/tnorlund/Portfolio/blob/0e5fdf394042873dbd99e64688962f88fbfb97d1/portfolio/types/api.ts#L418)

#### device

> **device**: `string`

#### model\_name

> **model\_name**: `string`

#### s3\_uri

> **s3\_uri**: `string`

***

### original

> **original**: `object`

Defined in: [types/api.ts:393](https://github.com/tnorlund/Portfolio/blob/0e5fdf394042873dbd99e64688962f88fbfb97d1/portfolio/types/api.ts#L393)

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

Defined in: [types/api.ts:392](https://github.com/tnorlund/Portfolio/blob/0e5fdf394042873dbd99e64688962f88fbfb97d1/portfolio/types/api.ts#L392)
