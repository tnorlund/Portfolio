[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/performance/testing](../README.md) / profileFunction

# Function: profileFunction()

> **profileFunction**\<`T`\>(`name`, `fn`): `Promise`\<\{ `profile`: [`PerformanceProfile`](../interfaces/PerformanceProfile.md); `result`: `T`; \}\>

Defined in: [utils/performance/testing.ts:137](https://github.com/tnorlund/Portfolio/blob/aa45bbd565c39ad52c3dd59050286813ccf4b2ea/portfolio/utils/performance/testing.ts#L137)

Profile a function and get detailed performance breakdown

## Type Parameters

### T

`T`

## Parameters

### name

`string`

### fn

() => `T` \| `Promise`\<`T`\>

## Returns

`Promise`\<\{ `profile`: [`PerformanceProfile`](../interfaces/PerformanceProfile.md); `result`: `T`; \}\>
