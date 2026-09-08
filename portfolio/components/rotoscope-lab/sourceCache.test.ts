import { LabSourceCache } from "./sourceCache";

test("reuses one source identity and invalidates it when the portrait changes", () => {
  const cache = new LabSourceCache();
  const first = Uint8ClampedArray.from([1, 2, 3, 255]);
  const duplicate = Uint8ClampedArray.from([9, 9, 9, 255]);
  const different = Uint8ClampedArray.from([4, 5, 6, 255]);

  expect(cache.store("portrait-a", 1, 1, first).changed).toBe(true);
  expect(cache.get("portrait-a", 1, 1)?.pixels).toBe(first);
  expect(cache.store("portrait-a", 1, 1, duplicate).changed).toBe(false);
  expect(cache.get("portrait-a", 1, 1)?.pixels).toBe(first);

  expect(cache.store("portrait-b", 1, 1, different).changed).toBe(true);
  expect(cache.get("portrait-a", 1, 1)).toBeNull();
  expect(cache.get("portrait-b", 1, 1)?.pixels).toBe(different);
});
