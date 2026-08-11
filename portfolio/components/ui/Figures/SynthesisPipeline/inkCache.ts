/**
 * Cache knocked-out receipt ink as ImageBitmaps keyed by source URL.
 * Avoids repeating decode + getImageData + knockout when the same print/cloud
 * is remounted across act transitions or React Strict Mode double-effects.
 */

type CacheEntry = {
  bitmap: ImageBitmap;
  width: number;
  height: number;
};

const cache = new Map<string, CacheEntry>();
const inflight = new Map<string, Promise<CacheEntry | null>>();

const canUseImageBitmap = (): boolean =>
  typeof createImageBitmap === "function" && typeof ImageBitmap !== "undefined";

/**
 * Decode `src` to pixels, run `process` (knockout), and return a transferable
 * ImageBitmap. Falls back to null when ImageBitmap APIs are unavailable
 * (jsdom); callers should use the canvas getImageData path instead.
 */
export const getKnockedOutInkBitmap = (
  src: string,
  process: (pixels: Uint8ClampedArray, width: number, height: number) => Promise<void>,
): Promise<CacheEntry | null> => {
  const hit = cache.get(src);
  if (hit) {
    return Promise.resolve(hit);
  }
  const pending = inflight.get(src);
  if (pending) {
    return pending;
  }

  const work = (async (): Promise<CacheEntry | null> => {
    if (!canUseImageBitmap()) {
      return null;
    }
    try {
      const response = await fetch(src);
      if (!response.ok) {
        return null;
      }
      const blob = await response.blob();
      const decoded = await createImageBitmap(blob);
      const width = decoded.width;
      const height = decoded.height;

      // Prefer OffscreenCanvas so we never touch the visible canvas for readback.
      let pixels: Uint8ClampedArray;
      if (typeof OffscreenCanvas !== "undefined") {
        const off = new OffscreenCanvas(width, height);
        const ctx = off.getContext("2d", { willReadFrequently: true });
        if (!ctx) {
          decoded.close();
          return null;
        }
        ctx.drawImage(decoded, 0, 0);
        decoded.close();
        const imageData = ctx.getImageData(0, 0, width, height);
        pixels = imageData.data;
        await process(pixels, width, height);
        ctx.putImageData(imageData, 0, 0);
        const bitmap = await off.transferToImageBitmap();
        const entry = { bitmap, width, height };
        cache.set(src, entry);
        return entry;
      }

      // DOM canvas fallback when OffscreenCanvas is missing.
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) {
        decoded.close();
        return null;
      }
      ctx.drawImage(decoded, 0, 0);
      decoded.close();
      const imageData = ctx.getImageData(0, 0, width, height);
      await process(imageData.data, width, height);
      ctx.putImageData(imageData, 0, 0);
      const bitmap = await createImageBitmap(canvas);
      const entry = { bitmap, width, height };
      cache.set(src, entry);
      return entry;
    } catch {
      return null;
    } finally {
      inflight.delete(src);
    }
  })();

  inflight.set(src, work);
  return work;
};

/** Test-only: drop cached bitmaps. */
export const __clearInkCacheForTests = (): void => {
  cache.forEach((entry) => {
    try {
      entry.bitmap.close();
    } catch {
      // ignore
    }
  });
  cache.clear();
  inflight.clear();
};
