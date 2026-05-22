[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationWord

# Interface: LabelValidationWord

Defined in: [types/api.ts:628](https://github.com/tnorlund/Portfolio/blob/d46db4b0ff4323af25de5f5023e87cdd1c741ae3/portfolio/types/api.ts#L628)

Individual word validation result from the label validation pipeline.
Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).

## Properties

### bbox

> **bbox**: `object`

Defined in: [types/api.ts:632](https://github.com/tnorlund/Portfolio/blob/d46db4b0ff4323af25de5f5023e87cdd1c741ae3/portfolio/types/api.ts#L632)

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

Defined in: [types/api.ts:641](https://github.com/tnorlund/Portfolio/blob/d46db4b0ff4323af25de5f5023e87cdd1c741ae3/portfolio/types/api.ts#L641)

***

### label

> **label**: `string`

Defined in: [types/api.ts:638](https://github.com/tnorlund/Portfolio/blob/d46db4b0ff4323af25de5f5023e87cdd1c741ae3/portfolio/types/api.ts#L638)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:630](https://github.com/tnorlund/Portfolio/blob/d46db4b0ff4323af25de5f5023e87cdd1c741ae3/portfolio/types/api.ts#L630)

***

### text

> **text**: `string`

Defined in: [types/api.ts:629](https://github.com/tnorlund/Portfolio/blob/d46db4b0ff4323af25de5f5023e87cdd1c741ae3/portfolio/types/api.ts#L629)

***

### validation\_source

> **validation\_source**: `"chroma"` \| `"llm"` \| `null`

Defined in: [types/api.ts:640](https://github.com/tnorlund/Portfolio/blob/d46db4b0ff4323af25de5f5023e87cdd1c741ae3/portfolio/types/api.ts#L640)

***

### word\_id

> **word\_id**: `number`

Defined in: [types/api.ts:631](https://github.com/tnorlund/Portfolio/blob/d46db4b0ff4323af25de5f5023e87cdd1c741ae3/portfolio/types/api.ts#L631)

***

### validation\_status?

> `optional` **validation\_status**: `"PENDING"` \| `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"NONE"`

Defined in: [types/api.ts:639](https://github.com/tnorlund/Portfolio/blob/d46db4b0ff4323af25de5f5023e87cdd1c741ae3/portfolio/types/api.ts#L639)
