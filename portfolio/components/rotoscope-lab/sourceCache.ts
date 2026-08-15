export interface LabSourceCacheEntry {
  key: string;
  pixels: Uint8ClampedArray;
}

export const labSourceCacheKey = (
  sourceKey: string,
  width: number,
  height: number,
): string => `${sourceKey}:${width}x${height}`;

/** One immutable decoded portrait, bounded to a single source and size. */
export class LabSourceCache {
  private entry: LabSourceCacheEntry | null = null;

  get(sourceKey: string, width: number, height: number): LabSourceCacheEntry | null {
    const key = labSourceCacheKey(sourceKey, width, height);
    return this.entry?.key === key ? this.entry : null;
  }

  store(
    sourceKey: string,
    width: number,
    height: number,
    pixels: Uint8ClampedArray,
  ): { entry: LabSourceCacheEntry; changed: boolean } {
    const key = labSourceCacheKey(sourceKey, width, height);
    if (this.entry?.key === key) return { entry: this.entry, changed: false };
    this.entry = { key, pixels };
    return { entry: this.entry, changed: true };
  }
}
