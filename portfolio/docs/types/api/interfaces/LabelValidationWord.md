[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelValidationWord

# Interface: LabelValidationWord

Defined in: [types/api.ts:573](https://github.com/tnorlund/Portfolio/blob/0aa1147b2f9eb98806809a4a997155b9aca74b49/portfolio/types/api.ts#L573)

Individual word validation result from the label validation pipeline.
Each word is validated by ChromaDB consensus (Tier 1) or LLM (Tier 2).

## Properties

### bbox

> **bbox**: `object`

Defined in: [types/api.ts:577](https://github.com/tnorlund/Portfolio/blob/0aa1147b2f9eb98806809a4a997155b9aca74b49/portfolio/types/api.ts#L577)

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

Defined in: [types/api.ts:586](https://github.com/tnorlund/Portfolio/blob/0aa1147b2f9eb98806809a4a997155b9aca74b49/portfolio/types/api.ts#L586)

***

### label

> **label**: `string`

Defined in: [types/api.ts:583](https://github.com/tnorlund/Portfolio/blob/0aa1147b2f9eb98806809a4a997155b9aca74b49/portfolio/types/api.ts#L583)

***

### line\_id

> **line\_id**: `number`

Defined in: [types/api.ts:575](https://github.com/tnorlund/Portfolio/blob/0aa1147b2f9eb98806809a4a997155b9aca74b49/portfolio/types/api.ts#L575)

***

### text

> **text**: `string`

Defined in: [types/api.ts:574](https://github.com/tnorlund/Portfolio/blob/0aa1147b2f9eb98806809a4a997155b9aca74b49/portfolio/types/api.ts#L574)

***

### validation\_source

> **validation\_source**: `"chroma"` \| `"llm"` \| `null`

Defined in: [types/api.ts:585](https://github.com/tnorlund/Portfolio/blob/0aa1147b2f9eb98806809a4a997155b9aca74b49/portfolio/types/api.ts#L585)

***

### word\_id

> **word\_id**: `number`

Defined in: [types/api.ts:576](https://github.com/tnorlund/Portfolio/blob/0aa1147b2f9eb98806809a4a997155b9aca74b49/portfolio/types/api.ts#L576)

***

### validation\_status?

> `optional` **validation\_status**: `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"` \| `"NONE"` \| `"PENDING"`

Defined in: [types/api.ts:584](https://github.com/tnorlund/Portfolio/blob/0aa1147b2f9eb98806809a4a997155b9aca74b49/portfolio/types/api.ts#L584)
