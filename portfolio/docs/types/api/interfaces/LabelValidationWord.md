[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationWord

# Interface: LabelValidationWord

Defined in: [types/api.ts:736](https://github.com/tnorlund/Portfolio/blob/f2436bfcff79867f7b7c02d290f8ebe0967bae57/portfolio/types/api.ts#L736)

Individual word validation result from the label validation pipeline.
Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).

## Properties

### bbox

> **bbox**: `object`

Defined in: [types/api.ts:740](https://github.com/tnorlund/Portfolio/blob/f2436bfcff79867f7b7c02d290f8ebe0967bae57/portfolio/types/api.ts#L740)

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

Defined in: [types/api.ts:749](https://github.com/tnorlund/Portfolio/blob/f2436bfcff79867f7b7c02d290f8ebe0967bae57/portfolio/types/api.ts#L749)

***

### label

> **label**: `string`

Defined in: [types/api.ts:746](https://github.com/tnorlund/Portfolio/blob/f2436bfcff79867f7b7c02d290f8ebe0967bae57/portfolio/types/api.ts#L746)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:738](https://github.com/tnorlund/Portfolio/blob/f2436bfcff79867f7b7c02d290f8ebe0967bae57/portfolio/types/api.ts#L738)

***

### text

> **text**: `string`

Defined in: [types/api.ts:737](https://github.com/tnorlund/Portfolio/blob/f2436bfcff79867f7b7c02d290f8ebe0967bae57/portfolio/types/api.ts#L737)

***

### validation\_source

> **validation\_source**: `"chroma"` \| `"llm"` \| `null`

Defined in: [types/api.ts:748](https://github.com/tnorlund/Portfolio/blob/f2436bfcff79867f7b7c02d290f8ebe0967bae57/portfolio/types/api.ts#L748)

***

### word\_id

> **word\_id**: `number`

Defined in: [types/api.ts:739](https://github.com/tnorlund/Portfolio/blob/f2436bfcff79867f7b7c02d290f8ebe0967bae57/portfolio/types/api.ts#L739)

***

### validation\_status?

> `optional` **validation\_status**: `"PENDING"` \| `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"NONE"`

Defined in: [types/api.ts:747](https://github.com/tnorlund/Portfolio/blob/f2436bfcff79867f7b7c02d290f8ebe0967bae57/portfolio/types/api.ts#L747)
