[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:582](https://github.com/tnorlund/Portfolio/blob/deb76bb88afd8900e4b759aa92665a46da68b66a/portfolio/types/api.ts#L582)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:583](https://github.com/tnorlund/Portfolio/blob/deb76bb88afd8900e4b759aa92665a46da68b66a/portfolio/types/api.ts#L583)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:585](https://github.com/tnorlund/Portfolio/blob/deb76bb88afd8900e4b759aa92665a46da68b66a/portfolio/types/api.ts#L585)

#### current\_label

> **current\_label**: `string`

#### line\_id

> **line\_id**: `number`

#### word\_id

> **word\_id**: `number`

#### word\_text

> **word\_text**: `string`

***

### llm\_review

> **llm\_review**: `object`

Defined in: [types/api.ts:591](https://github.com/tnorlund/Portfolio/blob/deb76bb88afd8900e4b759aa92665a46da68b66a/portfolio/types/api.ts#L591)

#### confidence

> **confidence**: `"high"` \| `"medium"` \| `"low"`

#### decision

> **decision**: `"VALID"` \| `"INVALID"` \| `"NEEDS_REVIEW"`

#### reasoning

> **reasoning**: `string`

#### suggested\_label

> **suggested\_label**: `string` \| `null`

***

### receipt\_id

> **receipt\_id**: `number`

Defined in: [types/api.ts:584](https://github.com/tnorlund/Portfolio/blob/deb76bb88afd8900e4b759aa92665a46da68b66a/portfolio/types/api.ts#L584)
