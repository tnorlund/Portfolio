[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationWord

# Interface: LabelValidationWord

Defined in: [types/api.ts:566](https://github.com/tnorlund/Portfolio/blob/ab4685305ca676a0b334728139d18e0302d6801a/portfolio/types/api.ts#L566)

Individual word validation result from the label validation pipeline.
Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).

## Properties

### bbox

> **bbox**: `object`

Defined in: [types/api.ts:570](https://github.com/tnorlund/Portfolio/blob/ab4685305ca676a0b334728139d18e0302d6801a/portfolio/types/api.ts#L570)

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

Defined in: [types/api.ts:579](https://github.com/tnorlund/Portfolio/blob/ab4685305ca676a0b334728139d18e0302d6801a/portfolio/types/api.ts#L579)

***

### label

> **label**: `string`

Defined in: [types/api.ts:576](https://github.com/tnorlund/Portfolio/blob/ab4685305ca676a0b334728139d18e0302d6801a/portfolio/types/api.ts#L576)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:568](https://github.com/tnorlund/Portfolio/blob/ab4685305ca676a0b334728139d18e0302d6801a/portfolio/types/api.ts#L568)

***

### text

> **text**: `string`

Defined in: [types/api.ts:567](https://github.com/tnorlund/Portfolio/blob/ab4685305ca676a0b334728139d18e0302d6801a/portfolio/types/api.ts#L567)

***

### validation\_source

> **validation\_source**: `"chroma"` \| `"llm"` \| `null`

Defined in: [types/api.ts:578](https://github.com/tnorlund/Portfolio/blob/ab4685305ca676a0b334728139d18e0302d6801a/portfolio/types/api.ts#L578)

***

### word\_id

> **word\_id**: `number`

Defined in: [types/api.ts:569](https://github.com/tnorlund/Portfolio/blob/ab4685305ca676a0b334728139d18e0302d6801a/portfolio/types/api.ts#L569)

***

### validation\_status?

> `optional` **validation\_status**: `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"NONE"` \| `"PENDING"`

Defined in: [types/api.ts:577](https://github.com/tnorlund/Portfolio/blob/ab4685305ca676a0b334728139d18e0302d6801a/portfolio/types/api.ts#L577)
