import { readFile } from "node:fs/promises";
import path from "node:path";
import { webcrypto } from "node:crypto";
import { TextDecoder } from "node:util";
import {
  parseVisionPortraitArtifacts,
  primaryFaceVisionFeatures,
  VISION_PORTRAIT_SOURCE_SHA256,
} from "./vision";

const artifact = (name: string) =>
  path.join(process.cwd(), "public", "rotoscope", name);

beforeAll(() => {
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: webcrypto,
  });
  Object.defineProperty(globalThis, "TextDecoder", {
    configurable: true,
    value: TextDecoder,
  });
});

test("accepts the canonical Apple Vision portrait artifacts", async () => {
  const manifest = JSON.parse(
    await readFile(artifact("vision-portrait-v1.json"), "utf8"),
  ) as Record<string, unknown>;
  const mask = await readFile(artifact("vision-person-mask-v1.json"));
  const parsed = await parseVisionPortraitArtifacts(
    manifest,
    mask.buffer.slice(mask.byteOffset, mask.byteOffset + mask.byteLength),
  );
  expect(parsed.source).toEqual({
    width: 960,
    height: 720,
    sha256: VISION_PORTRAIT_SOURCE_SHA256,
  });
  expect(parsed.primaryFace.landmarkRegions).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ name: "faceContour" }),
      expect.objectContaining({ name: "leftEye" }),
      expect.objectContaining({ name: "rightEye" }),
      expect.objectContaining({ name: "nose" }),
      expect.objectContaining({ name: "outerLips" }),
    ]),
  );
  expect(parsed.features.filter((feature) => feature.kind === "face-landmark"))
    .toHaveLength(76);
  expect(parsed.features.filter((feature) => feature.kind === "body-joint"))
    .toHaveLength(33);
  const primaryFaceFeatures = primaryFaceVisionFeatures(parsed);
  expect(primaryFaceFeatures).toHaveLength(77);
  expect(
    primaryFaceFeatures.every(
      (feature) =>
        feature.kind === "face-landmark" || feature.kind === "face-center",
    ),
  ).toBe(true);
  expect(parsed.mask.pixels).toHaveLength(240 * 180);
  const face = parsed.primaryFace.boundingBox;
  const faceX = Math.floor((face.x + face.width / 2) * parsed.mask.width);
  const faceY = Math.floor((face.y + face.height / 2) * parsed.mask.height);
  expect(parsed.mask.pixels[faceY * parsed.mask.width + faceX]).toBe(1);
});

test("rejects stale source geometry and malformed masks", async () => {
  const manifest = JSON.parse(
    await readFile(artifact("vision-portrait-v1.json"), "utf8"),
  ) as Record<string, unknown>;
  const mask = await readFile(artifact("vision-person-mask-v1.json"));
  const stale = JSON.parse(JSON.stringify(manifest)) as {
    source: { sha256: string };
  };
  stale.source.sha256 = "0".repeat(64);
  await expect(
    parseVisionPortraitArtifacts(
      stale,
      mask.buffer.slice(mask.byteOffset, mask.byteOffset + mask.byteLength),
    ),
  ).rejects.toThrow("does not match the portrait");

  const truncated = mask.subarray(0, mask.length - 1);
  await expect(
    parseVisionPortraitArtifacts(
      manifest,
      truncated.buffer.slice(
        truncated.byteOffset,
        truncated.byteOffset + truncated.byteLength,
      ),
    ),
  ).rejects.toThrow("hash mismatch");
});
