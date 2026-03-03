[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationWord

# Interface: LabelValidationWord

Defined in: [types/api.ts:625](https://github.com/tnorlund/Portfolio/blob/3a5044c9a2088b63a985d7c20f9d0a758802d507/portfolio/types/api.ts#L625)

Individual word validation result from the label validation pipeline.
Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).

## Properties

### bbox

> **bbox**: `object`

Defined in: [types/api.ts:629](https://github.com/tnorlund/Portfolio/blob/3a5044c9a2088b63a985d7c20f9d0a758802d507/portfolio/types/api.ts#L629)

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

Defined in: [types/api.ts:638](https://github.com/tnorlund/Portfolio/blob/3a5044c9a2088b63a985d7c20f9d0a758802d507/portfolio/types/api.ts#L638)

***

### label

> **label**: `string`

Defined in: [types/api.ts:635](https://github.com/tnorlund/Portfolio/blob/3a5044c9a2088b63a985d7c20f9d0a758802d507/portfolio/types/api.ts#L635)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:627](https://github.com/tnorlund/Portfolio/blob/3a5044c9a2088b63a985d7c20f9d0a758802d507/portfolio/types/api.ts#L627)

***

### text

> **text**: `string`

Defined in: [types/api.ts:626](https://github.com/tnorlund/Portfolio/blob/3a5044c9a2088b63a985d7c20f9d0a758802d507/portfolio/types/api.ts#L626)

***

### validation\_source

> **validation\_source**: `"chroma"` \| `"llm"` \| `null`

Defined in: [types/api.ts:637](https://github.com/tnorlund/Portfolio/blob/3a5044c9a2088b63a985d7c20f9d0a758802d507/portfolio/types/api.ts#L637)

***

### word\_id

> **word\_id**: `number`

Defined in: [types/api.ts:628](https://github.com/tnorlund/Portfolio/blob/3a5044c9a2088b63a985d7c20f9d0a758802d507/portfolio/types/api.ts#L628)

***

### validation\_status?

> `optional` **validation\_status**: `"PENDING"` \| `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"NONE"`

Defined in: [types/api.ts:636](https://github.com/tnorlund/Portfolio/blob/3a5044c9a2088b63a985d7c20f9d0a758802d507/portfolio/types/api.ts#L636)
