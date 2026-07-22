[**portfolio**](../../../README.md)

***

[portfolio](../../../modules.md) / [utils/imageFormat](../README.md) / usePreloadReceiptImages

# Function: usePreloadReceiptImages()

> **usePreloadReceiptImages**(`images`, `formatSupport`): `void`

Defined in: [utils/imageFormat.ts:211](https://github.com/tnorlund/Portfolio/blob/f2b21b6edbf2e02bd764abc0446de2e0b8cd1ba1/portfolio/utils/imageFormat.ts#L211)

Preload full-size, medium, and thumbnail images for all receipts so they're
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
