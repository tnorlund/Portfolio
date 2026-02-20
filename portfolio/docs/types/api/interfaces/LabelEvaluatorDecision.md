[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [types/api](../README.md) / LabelEvaluatorDecision

# Interface: LabelEvaluatorDecision

Defined in: [types/api.ts:497](https://github.com/tnorlund/Portfolio/blob/74e113da5b8c211adc8297cd39f89d5558ebd798/portfolio/types/api.ts#L497)

## Properties

### image\_id

> **image\_id**: `string`

Defined in: [types/api.ts:498](https://github.com/tnorlund/Portfolio/blob/74e113da5b8c211adc8297cd39f89d5558ebd798/portfolio/types/api.ts#L498)

***

### issue

> **issue**: `object`

Defined in: [types/api.ts:500](https://github.com/tnorlund/Portfolio/blob/74e113da5b8c211adc8297cd39f89d5558ebd798/portfolio/types/api.ts#L500)

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

Defined in: [types/api.ts:506](https://github.com/tnorlund/Portfolio/blob/74e113da5b8c211adc8297cd39f89d5558ebd798/portfolio/types/api.ts#L506)

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

Defined in: [types/api.ts:499](https://github.com/tnorlund/Portfolio/blob/74e113da5b8c211adc8297cd39f89d5558ebd798/portfolio/types/api.ts#L499)
