[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationWord

# Interface: LabelValidationWord

Defined in: [types/api.ts:622](https://github.com/tnorlund/Portfolio/blob/eb94a79fdc210f3a886f04d90ce74a35ac0d9fb3/portfolio/types/api.ts#L622)

Individual word validation result from the label validation pipeline.
Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).

## Properties

### bbox

> **bbox**: `object`

Defined in: [types/api.ts:626](https://github.com/tnorlund/Portfolio/blob/eb94a79fdc210f3a886f04d90ce74a35ac0d9fb3/portfolio/types/api.ts#L626)

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

Defined in: [types/api.ts:635](https://github.com/tnorlund/Portfolio/blob/eb94a79fdc210f3a886f04d90ce74a35ac0d9fb3/portfolio/types/api.ts#L635)

***

### label

> **label**: `string`

Defined in: [types/api.ts:632](https://github.com/tnorlund/Portfolio/blob/eb94a79fdc210f3a886f04d90ce74a35ac0d9fb3/portfolio/types/api.ts#L632)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:624](https://github.com/tnorlund/Portfolio/blob/eb94a79fdc210f3a886f04d90ce74a35ac0d9fb3/portfolio/types/api.ts#L624)

***

### text

> **text**: `string`

Defined in: [types/api.ts:623](https://github.com/tnorlund/Portfolio/blob/eb94a79fdc210f3a886f04d90ce74a35ac0d9fb3/portfolio/types/api.ts#L623)

***

### validation\_source

> **validation\_source**: `"chroma"` \| `"llm"` \| `null`

Defined in: [types/api.ts:634](https://github.com/tnorlund/Portfolio/blob/eb94a79fdc210f3a886f04d90ce74a35ac0d9fb3/portfolio/types/api.ts#L634)

***

### word\_id

> **word\_id**: `number`

Defined in: [types/api.ts:625](https://github.com/tnorlund/Portfolio/blob/eb94a79fdc210f3a886f04d90ce74a35ac0d9fb3/portfolio/types/api.ts#L625)

***

### validation\_status?

> `optional` **validation\_status**: `"PENDING"` \| `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"NONE"`

Defined in: [types/api.ts:633](https://github.com/tnorlund/Portfolio/blob/eb94a79fdc210f3a886f04d90ce74a35ac0d9fb3/portfolio/types/api.ts#L633)
