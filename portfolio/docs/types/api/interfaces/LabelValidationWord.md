[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationWord

# Interface: LabelValidationWord

Defined in: [types/api.ts:705](https://github.com/tnorlund/Portfolio/blob/faea9b9c08941ae9be43baf8768c5cfa8d5b4fe8/portfolio/types/api.ts#L705)

Individual word validation result from the label validation pipeline.
Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).

## Properties

### bbox

> **bbox**: `object`

Defined in: [types/api.ts:709](https://github.com/tnorlund/Portfolio/blob/faea9b9c08941ae9be43baf8768c5cfa8d5b4fe8/portfolio/types/api.ts#L709)

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

Defined in: [types/api.ts:718](https://github.com/tnorlund/Portfolio/blob/faea9b9c08941ae9be43baf8768c5cfa8d5b4fe8/portfolio/types/api.ts#L718)

***

### label

> **label**: `string`

Defined in: [types/api.ts:715](https://github.com/tnorlund/Portfolio/blob/faea9b9c08941ae9be43baf8768c5cfa8d5b4fe8/portfolio/types/api.ts#L715)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:707](https://github.com/tnorlund/Portfolio/blob/faea9b9c08941ae9be43baf8768c5cfa8d5b4fe8/portfolio/types/api.ts#L707)

***

### text

> **text**: `string`

Defined in: [types/api.ts:706](https://github.com/tnorlund/Portfolio/blob/faea9b9c08941ae9be43baf8768c5cfa8d5b4fe8/portfolio/types/api.ts#L706)

***

### validation\_source

> **validation\_source**: `"chroma"` \| `"llm"` \| `null`

Defined in: [types/api.ts:717](https://github.com/tnorlund/Portfolio/blob/faea9b9c08941ae9be43baf8768c5cfa8d5b4fe8/portfolio/types/api.ts#L717)

***

### word\_id

> **word\_id**: `number`

Defined in: [types/api.ts:708](https://github.com/tnorlund/Portfolio/blob/faea9b9c08941ae9be43baf8768c5cfa8d5b4fe8/portfolio/types/api.ts#L708)

***

### validation\_status?

> `optional` **validation\_status**: `"PENDING"` \| `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"NONE"`

Defined in: [types/api.ts:716](https://github.com/tnorlund/Portfolio/blob/faea9b9c08941ae9be43baf8768c5cfa8d5b4fe8/portfolio/types/api.ts#L716)
