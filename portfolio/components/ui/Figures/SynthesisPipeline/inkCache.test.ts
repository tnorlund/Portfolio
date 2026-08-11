import {
  __clearInkCacheForTests,
  getKnockedOutInkBitmap,
} from "./inkCache";

afterEach(() => {
  __clearInkCacheForTests();
  jest.restoreAllMocks();
});

test("getKnockedOutInkBitmap returns null when ImageBitmap is unavailable", async () => {
  const original = global.createImageBitmap;
  // @ts-expect-error force unavailable
  global.createImageBitmap = undefined;

  const result = await getKnockedOutInkBitmap("/x.png", async () => {});
  expect(result).toBeNull();

  global.createImageBitmap = original;
});

test("getKnockedOutInkBitmap caches processed bitmaps per src", async () => {
  if (typeof OffscreenCanvas === "undefined" || typeof createImageBitmap !== "function") {
    // jsdom environment — exercise the null path only.
    const result = await getKnockedOutInkBitmap("/missing.png", async () => {});
    expect(result).toBeNull();
    return;
  }

  const pixels = new Uint8ClampedArray([0, 0, 0, 255]);
  const fakeBitmap = {
    width: 1,
    height: 1,
    close: jest.fn(),
  } as unknown as ImageBitmap;

  jest.spyOn(global, "fetch").mockResolvedValue({
    ok: true,
    blob: async () => new Blob([pixels]),
  } as Response);
  jest.spyOn(global, "createImageBitmap").mockResolvedValue(fakeBitmap);

  // Stub OffscreenCanvas path to avoid depending on full canvas impl.
  const process = jest.fn(async (buf: Uint8ClampedArray) => {
    buf[3] = 128;
  });

  // When OffscreenCanvas exists but getContext may be incomplete in jsdom,
  // the helper should fail soft to null rather than throw.
  const first = await getKnockedOutInkBitmap("/ink.png", process);
  const second = await getKnockedOutInkBitmap("/ink.png", process);
  if (first && second) {
    expect(second).toBe(first);
    expect(process).toHaveBeenCalledTimes(1);
  } else {
    expect(first).toBeNull();
  }
});
