[**portfolio**](../../../../README.md)

***

[portfolio](../../../../modules.md) / [utils/performance/testing](../README.md) / PerformanceBenchmark

# Class: PerformanceBenchmark

Defined in: [utils/performance/testing.ts:212](https://github.com/tnorlund/Portfolio/blob/f2a9046d88b0fa8c1f3329c68a30c2d1f15d42d5/portfolio/utils/performance/testing.ts#L212)

Create a performance benchmark suite

## Constructors

### Constructor

> **new PerformanceBenchmark**(): `PerformanceBenchmark`

#### Returns

`PerformanceBenchmark`

## Methods

### add()

> **add**(`name`, `fn`, `options?`): `PerformanceBenchmark`

Defined in: [utils/performance/testing.ts:215](https://github.com/tnorlund/Portfolio/blob/f2a9046d88b0fa8c1f3329c68a30c2d1f15d42d5/portfolio/utils/performance/testing.ts#L215)

#### Parameters

##### name

`string`

##### fn

() => `void` \| `Promise`\<`void`\>

##### options?

`Partial`\<[`PerformanceTest`](../interfaces/PerformanceTest.md)\>

#### Returns

`PerformanceBenchmark`

***

### compare()

> **compare**(`baseline`): `Promise`\<`void`\>

Defined in: [utils/performance/testing.ts:236](https://github.com/tnorlund/Portfolio/blob/f2a9046d88b0fa8c1f3329c68a30c2d1f15d42d5/portfolio/utils/performance/testing.ts#L236)

#### Parameters

##### baseline

`string`

#### Returns

`Promise`\<`void`\>

***

### run()

> **run**(): `Promise`\<`Map`\<`string`, [`PerformanceTestResult`](../interfaces/PerformanceTestResult.md)\>\>

Defined in: [utils/performance/testing.ts:224](https://github.com/tnorlund/Portfolio/blob/f2a9046d88b0fa8c1f3329c68a30c2d1f15d42d5/portfolio/utils/performance/testing.ts#L224)

#### Returns

`Promise`\<`Map`\<`string`, [`PerformanceTestResult`](../interfaces/PerformanceTestResult.md)\>\>
