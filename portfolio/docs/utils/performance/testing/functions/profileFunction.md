[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/performance/testing](../README.md) / profileFunction

# Function: profileFunction()

> **profileFunction**\<`T`\>(`name`, `fn`): `Promise`\<\{ `profile`: [`PerformanceProfile`](../interfaces/PerformanceProfile.md); `result`: `T`; \}\>

Defined in: [utils/performance/testing.ts:137](https://github.com/tnorlund/Portfolio/blob/e1c2374974c84bace53a8104f9a62e1c07dbef9f/portfolio/utils/performance/testing.ts#L137)

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
