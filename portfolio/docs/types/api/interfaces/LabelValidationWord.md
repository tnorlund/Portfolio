[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationWord

# Interface: LabelValidationWord

Defined in: [types/api.ts:591](https://github.com/tnorlund/Portfolio/blob/7c9722dbcc72c3f54f5ed0a8590c604ecfc5b355/portfolio/types/api.ts#L591)

Individual word validation result from the label validation pipeline.
Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).

## Properties

### bbox

> **bbox**: `object`

Defined in: [types/api.ts:595](https://github.com/tnorlund/Portfolio/blob/7c9722dbcc72c3f54f5ed0a8590c604ecfc5b355/portfolio/types/api.ts#L595)

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

Defined in: [types/api.ts:604](https://github.com/tnorlund/Portfolio/blob/7c9722dbcc72c3f54f5ed0a8590c604ecfc5b355/portfolio/types/api.ts#L604)

***

### label

> **label**: `string`

Defined in: [types/api.ts:601](https://github.com/tnorlund/Portfolio/blob/7c9722dbcc72c3f54f5ed0a8590c604ecfc5b355/portfolio/types/api.ts#L601)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:593](https://github.com/tnorlund/Portfolio/blob/7c9722dbcc72c3f54f5ed0a8590c604ecfc5b355/portfolio/types/api.ts#L593)

***

### text

> **text**: `string`

Defined in: [types/api.ts:592](https://github.com/tnorlund/Portfolio/blob/7c9722dbcc72c3f54f5ed0a8590c604ecfc5b355/portfolio/types/api.ts#L592)

***

### validation\_source

> **validation\_source**: `"chroma"` \| `"llm"` \| `null`

Defined in: [types/api.ts:603](https://github.com/tnorlund/Portfolio/blob/7c9722dbcc72c3f54f5ed0a8590c604ecfc5b355/portfolio/types/api.ts#L603)

***

### word\_id

> **word\_id**: `number`

Defined in: [types/api.ts:594](https://github.com/tnorlund/Portfolio/blob/7c9722dbcc72c3f54f5ed0a8590c604ecfc5b355/portfolio/types/api.ts#L594)

***

### validation\_status?

> `optional` **validation\_status**: `"PENDING"` \| `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"NONE"`

Defined in: [types/api.ts:602](https://github.com/tnorlund/Portfolio/blob/7c9722dbcc72c3f54f5ed0a8590c604ecfc5b355/portfolio/types/api.ts#L602)
