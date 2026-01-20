[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationWord

# Interface: LabelValidationWord

Defined in: [types/api.ts:571](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L571)

Individual word validation result from the label validation pipeline.
Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).

## Properties

### bbox

> **bbox**: `object`

Defined in: [types/api.ts:575](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L575)

#### height

> **height**: `number`

#### width

> **width**: `number`

#### x

> **x**: `number`

#### y

> **y**: `number`

***

### decision

> **decision**: `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"CORRECTED"` \| `null`

Defined in: [types/api.ts:584](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L584)

***

### label

> **label**: `string`

Defined in: [types/api.ts:581](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L581)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:573](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L573)

***

### text

> **text**: `string`

Defined in: [types/api.ts:572](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L572)

***

### validation\_source

> **validation\_source**: `"chroma"` \| `"llm"` \| `null`

Defined in: [types/api.ts:583](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L583)

***

### word\_id

> **word\_id**: `number`

Defined in: [types/api.ts:574](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L574)

***

### validation\_status?

> `optional` **validation\_status**: `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"NONE"` \| `"PENDING"`

Defined in: [types/api.ts:582](https://github.com/tnorlund/Portfolio/blob/84481b2e2dc80af6916ba1258d612df40e5fcbbe/portfolio/types/api.ts#L582)
