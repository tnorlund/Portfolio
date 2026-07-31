[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationWord

# Interface: LabelValidationWord

Defined in: [types/api.ts:808](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L808)

Individual word validation result from the label validation pipeline.
Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).

## Properties

### bbox

> **bbox**: `object`

Defined in: [types/api.ts:812](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L812)

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

Defined in: [types/api.ts:821](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L821)

***

### label

> **label**: `string`

Defined in: [types/api.ts:818](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L818)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:810](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L810)

***

### text

> **text**: `string`

Defined in: [types/api.ts:809](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L809)

***

### validation\_source

> **validation\_source**: `"chroma"` \| `"llm"` \| `null`

Defined in: [types/api.ts:820](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L820)

***

### word\_id

> **word\_id**: `number`

Defined in: [types/api.ts:811](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L811)

***

### validation\_status?

> `optional` **validation\_status**: `"PENDING"` \| `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"NONE"`

Defined in: [types/api.ts:819](https://github.com/tnorlund/Portfolio/blob/e36a64a94a66d29102da30b6c0e4a00f4af2919e/portfolio/types/api.ts#L819)
