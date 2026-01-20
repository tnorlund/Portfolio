[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LayoutLMReceiptInference

# Interface: LayoutLMReceiptInference

Defined in: [types/api.ts:396](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L396)

## Properties

### cached\_at

> **cached\_at**: `string`

Defined in: [types/api.ts:430](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L430)

***

### entities\_summary

> **entities\_summary**: [`LayoutLMEntitiesSummary`](LayoutLMEntitiesSummary.md)

Defined in: [types/api.ts:428](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L428)

***

### inference\_time\_ms

> **inference\_time\_ms**: `number`

Defined in: [types/api.ts:429](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L429)

***

### metrics

> **metrics**: `object`

Defined in: [types/api.ts:415](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L415)

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

Defined in: [types/api.ts:423](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L423)

#### device

> **device**: `string`

#### model\_name

> **model\_name**: `string`

#### s3\_uri

> **s3\_uri**: `string`

***

### original

> **original**: `object`

Defined in: [types/api.ts:398](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L398)

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

Defined in: [types/api.ts:397](https://github.com/tnorlund/Portfolio/blob/c9029eeb34926283633a40974df5d7db81b15a8a/portfolio/types/api.ts#L397)
