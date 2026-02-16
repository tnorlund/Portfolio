[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationWord

# Interface: LabelValidationWord

Defined in: [types/api.ts:623](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L623)

Individual word validation result from the label validation pipeline.
Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).

## Properties

### bbox

> **bbox**: `object`

Defined in: [types/api.ts:627](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L627)

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

Defined in: [types/api.ts:636](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L636)

***

### label

> **label**: `string`

Defined in: [types/api.ts:633](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L633)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:625](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L625)

***

### text

> **text**: `string`

Defined in: [types/api.ts:624](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L624)

***

### validation\_source

> **validation\_source**: `"chroma"` \| `"llm"` \| `null`

Defined in: [types/api.ts:635](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L635)

***

### word\_id

> **word\_id**: `number`

Defined in: [types/api.ts:626](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L626)

***

### validation\_status?

> `optional` **validation\_status**: `"PENDING"` \| `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"NONE"`

Defined in: [types/api.ts:634](https://github.com/tnorlund/Portfolio/blob/a229c1a8347a5b8c1a97fd004a13ba2fe1d47c26/portfolio/types/api.ts#L634)
