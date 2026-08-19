import type {
  BasinRevealFeatureRegion,
  BasinRevealPersonMask,
} from "./reveal";

export const PORTRAIT_PERSON_MASK_PATH =
  "/rotoscope/vision-person-mask-v1.json" as const;

/**
 * Compact regions distilled from Apple Vision's primary-face landmark groups.
 * The full 106 KB authoring manifest stays out of the homepage worker.
 */
export const PORTRAIT_PRIMARY_FEATURES: readonly (BasinRevealFeatureRegion & {
  name: "left-eye" | "right-eye" | "nose" | "mouth";
})[] = [
  {
    name: "left-eye",
    centerX: 0.3666,
    centerY: 0.5182,
    radiusX: 0.029,
    radiusY: 0.022,
    order: 0,
  },
  {
    name: "right-eye",
    centerX: 0.4557,
    centerY: 0.5254,
    radiusX: 0.029,
    radiusY: 0.022,
    order: 0,
  },
  {
    name: "nose",
    centerX: 0.4108,
    centerY: 0.572,
    radiusX: 0.036,
    radiusY: 0.063,
    order: 1,
  },
  {
    name: "mouth",
    centerX: 0.4046,
    centerY: 0.6797,
    radiusX: 0.057,
    radiusY: 0.052,
    order: 2,
  },
] as const;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

export const decodePortraitPersonMask = (
  value: unknown,
): BasinRevealPersonMask => {
  if (
    !isRecord(value) ||
    value.coordinateSpace !== "normalized-top-left" ||
    !Number.isInteger(value.width) ||
    !Number.isInteger(value.height)
  ) {
    throw new Error("invalid portrait person mask");
  }
  const width = value.width as number;
  const height = value.height as number;
  const runs = value.runs;
  if (
    width <= 0 ||
    height <= 0 ||
    width > 1024 ||
    height > 1024 ||
    !Array.isArray(runs) ||
    runs.length === 0 ||
    runs.length % 2 !== 0
  ) {
    throw new Error("invalid portrait person mask");
  }

  const pixels = new Uint8Array(width * height);
  let outputIndex = 0;
  for (let run = 0; run < runs.length; run += 2) {
    const pixel = runs[run];
    const length = runs[run + 1];
    if (
      (pixel !== 0 && pixel !== 1) ||
      !Number.isInteger(length) ||
      length <= 0 ||
      outputIndex + length > pixels.length
    ) {
      throw new Error("invalid portrait person mask");
    }
    pixels.fill(pixel, outputIndex, outputIndex + length);
    outputIndex += length;
  }
  if (outputIndex !== pixels.length) {
    throw new Error("invalid portrait person mask");
  }
  return { width, height, pixels };
};
