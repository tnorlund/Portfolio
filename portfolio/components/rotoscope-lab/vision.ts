export const VISION_PORTRAIT_MANIFEST_URL =
  "/rotoscope/vision-portrait-v1.json";
export const VISION_PORTRAIT_SOURCE_SHA256 =
  "ba3fd0cd5920876b639846ca37f6fc2a8a393a6e32ad0d76b7e018113339fef9";

export type VisionFeatureKind =
  | "face-landmark"
  | "body-joint"
  | "hand-joint"
  | "face-center"
  | "human-center"
  | "saliency"
  | "contour";

export interface VisionPoint {
  x: number;
  y: number;
}

export interface VisionFeature {
  id: string;
  label: string;
  kind: VisionFeatureKind;
  group: string;
  point: VisionPoint;
  confidence: number;
}

export interface VisionRect extends VisionPoint {
  width: number;
  height: number;
}

export interface VisionPortraitArtifacts {
  source: { width: number; height: number; sha256: string };
  primaryFace: {
    boundingBox: VisionRect;
    captureQuality: number | null;
    landmarkRegions: { name: string; points: VisionPoint[] }[];
  };
  features: VisionFeature[];
  mask: {
    width: number;
    height: number;
    pixels: Uint8Array;
    boundingBox: VisionRect;
  };
}

type JsonRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is JsonRecord =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const finite = (value: unknown, label: string): number => {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`invalid Vision ${label}`);
  }
  return value;
};

const bounded = (value: unknown, label: string): number => {
  const number = finite(value, label);
  if (number < 0 || number > 1) throw new Error(`invalid Vision ${label}`);
  return number;
};

const integer = (
  value: unknown,
  label: string,
  minimum: number,
  maximum: number,
): number => {
  const number = finite(value, label);
  if (!Number.isInteger(number) || number < minimum || number > maximum) {
    throw new Error(`invalid Vision ${label}`);
  }
  return number;
};

const string = (value: unknown, label: string, maximum = 128): string => {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > maximum
  ) {
    throw new Error(`invalid Vision ${label}`);
  }
  return value;
};

const point = (value: unknown, label: string): VisionPoint => {
  if (!isRecord(value)) throw new Error(`invalid Vision ${label}`);
  return {
    x: bounded(value.x, `${label}.x`),
    y: bounded(value.y, `${label}.y`),
  };
};

const rect = (value: unknown, label: string): VisionRect => {
  if (!isRecord(value)) throw new Error(`invalid Vision ${label}`);
  const parsed = {
    ...point(value, label),
    width: bounded(value.width, `${label}.width`),
    height: bounded(value.height, `${label}.height`),
  };
  if (parsed.x + parsed.width > 1.000001 || parsed.y + parsed.height > 1.000001) {
    throw new Error(`invalid Vision ${label}`);
  }
  return parsed;
};

const featureKinds = new Set<VisionFeatureKind>([
  "face-landmark",
  "body-joint",
  "hand-joint",
  "face-center",
  "human-center",
  "saliency",
  "contour",
]);

const parseFeature = (value: unknown, index: number): VisionFeature => {
  if (!isRecord(value)) throw new Error(`invalid Vision feature ${index}`);
  const kind = string(value.kind, `feature ${index} kind`) as VisionFeatureKind;
  if (!featureKinds.has(kind)) throw new Error(`invalid Vision feature ${index} kind`);
  return {
    id: string(value.id, `feature ${index} id`, 160),
    label: string(value.label, `feature ${index} label`, 80),
    kind,
    group: string(value.group, `feature ${index} group`, 80),
    point: point(value.point, `feature ${index} point`),
    confidence: bounded(value.confidence, `feature ${index} confidence`),
  };
};

const parseManifest = (
  value: unknown,
): Omit<VisionPortraitArtifacts, "mask"> & {
  maskReference: { path: string; sha256: string; width: number; height: number; boundingBox: VisionRect };
} => {
  if (!isRecord(value) || value.schemaVersion !== 1) {
    throw new Error("unsupported Vision portrait schema");
  }
  if (value.coordinateSpace !== "normalized-top-left") {
    throw new Error("unsupported Vision coordinate space");
  }
  if (!isRecord(value.source)) throw new Error("invalid Vision source");
  const source = {
    width: integer(value.source.width, "source width", 1, 4096),
    height: integer(value.source.height, "source height", 1, 4096),
    sha256: string(value.source.sha256, "source hash", 64),
  };
  if (
    source.width !== 960 ||
    source.height !== 720 ||
    source.sha256 !== VISION_PORTRAIT_SOURCE_SHA256
  ) {
    throw new Error("Vision artifact does not match the portrait");
  }
  if (!isRecord(value.primaryFace)) throw new Error("missing primary Vision face");
  const landmarkRegionsValue = value.primaryFace.landmarkRegions;
  if (!Array.isArray(landmarkRegionsValue) || landmarkRegionsValue.length > 20) {
    throw new Error("invalid Vision landmark regions");
  }
  const landmarkRegions = landmarkRegionsValue.map((region, regionIndex) => {
    if (!isRecord(region) || !Array.isArray(region.points) || region.points.length > 100) {
      throw new Error(`invalid Vision landmark region ${regionIndex}`);
    }
    return {
      name: string(region.name, `landmark region ${regionIndex}`, 40),
      points: region.points.map((entry, pointIndex) =>
        point(entry, `landmark region ${regionIndex} point ${pointIndex}`),
      ),
    };
  });
  const requiredRegions = [
    "faceContour",
    "leftEye",
    "rightEye",
    "leftEyebrow",
    "rightEyebrow",
    "nose",
    "outerLips",
    "innerLips",
    "allPoints",
  ];
  for (const required of requiredRegions) {
    if (!landmarkRegions.some((region) => region.name === required && region.points.length > 0)) {
      throw new Error(`missing Vision landmark region ${required}`);
    }
  }
  const allPoints = landmarkRegions.find((region) => region.name === "allPoints");
  if (!allPoints || new Set(allPoints.points.map((entry) => `${entry.x}:${entry.y}`)).size < 60) {
    throw new Error("too few unique Vision face landmarks");
  }
  if (!Array.isArray(value.features) || value.features.length < 60 || value.features.length > 512) {
    throw new Error("invalid Vision feature count");
  }
  const features = value.features.map(parseFeature);
  if (features.filter((entry) => entry.kind === "face-landmark").length < 60) {
    throw new Error("too few Vision face features");
  }
  if (!isRecord(value.personMask)) throw new Error("missing Vision person mask");
  if (value.personMask.containsPrimaryFaceCenter !== true) {
    throw new Error("Vision person mask does not contain the primary face");
  }
  const maskReference = {
    path: string(value.personMask.path, "person mask path", 160),
    sha256: string(value.personMask.sha256, "person mask hash", 64),
    width: integer(value.personMask.width, "person mask width", 1, 1024),
    height: integer(value.personMask.height, "person mask height", 1, 1024),
    boundingBox: rect(value.personMask.boundingBox, "person mask bounding box"),
  };
  if (!maskReference.path.startsWith("/rotoscope/")) {
    throw new Error("invalid Vision person mask path");
  }
  return {
    source,
    primaryFace: {
      boundingBox: rect(value.primaryFace.boundingBox, "primary face bounding box"),
      captureQuality:
        value.primaryFace.captureQuality === null
          ? null
          : bounded(value.primaryFace.captureQuality, "face capture quality"),
      landmarkRegions,
    },
    features,
    maskReference,
  };
};

const parseMask = (
  value: unknown,
  expected: { width: number; height: number },
): Uint8Array => {
  if (
    !isRecord(value) ||
    value.schemaVersion !== 1 ||
    value.coordinateSpace !== "normalized-top-left"
  ) {
    throw new Error("unsupported Vision person mask schema");
  }
  const width = integer(value.width, "person mask width", 1, 1024);
  const height = integer(value.height, "person mask height", 1, 1024);
  if (width !== expected.width || height !== expected.height || !Array.isArray(value.runs)) {
    throw new Error("Vision person mask dimensions do not match");
  }
  if (value.runs.length === 0 || value.runs.length > width * height * 2 || value.runs.length % 2) {
    throw new Error("invalid Vision person mask runs");
  }
  const pixels = new Uint8Array(width * height);
  let outputIndex = 0;
  for (let run = 0; run < value.runs.length; run += 2) {
    const pixel = integer(value.runs[run], "person mask pixel", 0, 1);
    const count = integer(value.runs[run + 1], "person mask run", 1, width * height);
    if (outputIndex + count > pixels.length) throw new Error("Vision person mask overflow");
    pixels.fill(pixel, outputIndex, outputIndex + count);
    outputIndex += count;
  }
  if (outputIndex !== pixels.length) throw new Error("Vision person mask is truncated");
  return pixels;
};

const sha256 = async (bytes: ArrayBuffer): Promise<string> => {
  if (!globalThis.crypto?.subtle) throw new Error("Vision artifact hashing unavailable");
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
};

export const parseVisionPortraitArtifacts = async (
  manifestValue: unknown,
  maskBytes: ArrayBuffer,
): Promise<VisionPortraitArtifacts> => {
  const parsed = parseManifest(manifestValue);
  if ((await sha256(maskBytes)) !== parsed.maskReference.sha256) {
    throw new Error("Vision person mask hash mismatch");
  }
  const maskValue = JSON.parse(new TextDecoder().decode(maskBytes)) as unknown;
  return {
    source: parsed.source,
    primaryFace: parsed.primaryFace,
    features: parsed.features,
    mask: {
      width: parsed.maskReference.width,
      height: parsed.maskReference.height,
      pixels: parseMask(maskValue, parsed.maskReference),
      boundingBox: parsed.maskReference.boundingBox,
    },
  };
};

export const loadVisionPortraitArtifacts = async (
  fetcher: typeof fetch = fetch,
): Promise<VisionPortraitArtifacts> => {
  const manifestResponse = await fetcher(VISION_PORTRAIT_MANIFEST_URL, {
    cache: "force-cache",
  });
  if (!manifestResponse.ok) {
    throw new Error(`Vision manifest request failed (${manifestResponse.status})`);
  }
  const manifestValue = (await manifestResponse.json()) as unknown;
  const parsed = parseManifest(manifestValue);
  const maskResponse = await fetcher(parsed.maskReference.path, {
    cache: "force-cache",
  });
  if (!maskResponse.ok) {
    throw new Error(`Vision mask request failed (${maskResponse.status})`);
  }
  return parseVisionPortraitArtifacts(manifestValue, await maskResponse.arrayBuffer());
};
