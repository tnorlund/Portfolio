[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [utils/imageFormat](../README.md) / usePreloadReceiptImages

# Function: usePreloadReceiptImages()

> **usePreloadReceiptImages**(`images`, `formatSupport`): `void`

Defined in: [utils/imageFormat.ts:206](https://github.com/tnorlund/Portfolio/blob/02e31a3a0806755573cf502e992bc265143c5268/portfolio/utils/imageFormat.ts#L206)

Preload full-size and thumbnail images for all receipts so they're
browser-cached before flying-receipt animations need them.
Uses a ref-based set to avoid re-fetching already-preloaded URLs,
which keeps it efficient for components that continuously append receipts.

## Parameters

### images

[`ImageFormats`](../interfaces/ImageFormats.md)[]

### formatSupport

[`FormatSupport`](../interfaces/FormatSupport.md) | `null`

## Returns

`void`
