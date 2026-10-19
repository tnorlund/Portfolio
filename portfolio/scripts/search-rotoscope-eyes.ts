import fs from "node:fs/promises";
import path from "node:path";
import { performance } from "node:perf_hooks";
import sharp from "sharp";
import {
  MAX_ROTOSCOPE_PIXELS,
  runRotoscope,
  type FocusGeometry,
  type RotoscopeOptions,
  type RotoscopeResult,
} from "../components/home/Rotoscope/algorithm";

const ROOT = path.join(__dirname, "..");
const SOURCE = path.join(ROOT, "public/rotoscope-portrait.jpg");
const OUT_DIR = path.join(ROOT, "..", "tmp", "rotoscope-eye-search");

const LEFT_EYE = { x: 0.3666, y: 0.5182, rx: 0.029, ry: 0.022 };
const RIGHT_EYE = { x: 0.4557, y: 0.5254, rx: 0.029, ry: 0.022 };
const EYE_PAD = 3;
const HEAD_FACE = {
  centerX: 0.4,
  centerY: 0.56,
  radiusX: 0.14,
  radiusY: 0.27,
} as const;

const PERIOCULAR_CENTER = {
  centerX: (LEFT_EYE.x + RIGHT_EYE.x) / 2,
  centerY: (LEFT_EYE.y + RIGHT_EYE.y) / 2,
} as const;

const HEAD_BODY: FocusGeometry["body"] = [
  [0.26, 0.3],
  [0.4, 0.26],
  [0.54, 0.3],
  [0.58, 0.52],
  [0.75, 0.82],
  [0.8, 1],
  [0, 1],
  [0, 0.84],
  [0.22, 0.52],
];

/** Frozen whole-head homepage pass before the periocular remap. */
const BASELINE_BODY: FocusGeometry["body"] = [
  [0.31, 0.67],
  [0.53, 0.66],
  [0.75, 0.82],
  [0.8, 1],
  [0, 1],
  [0, 0.84],
  [0.26, 0.72],
];

/** Current production homepage pass — periocular, already on main. */
const BASELINE_OPTIONS: Partial<RotoscopeOptions> = {
  blurRadius: 3,
  markerBudget: 1600,
  quotas: { face: 0.3, body: 0.64, background: 0.06 },
  spacing: { face: 1, body: 4, background: 8 },
  focus: {
    face: {
      centerX: 0.4112,
      centerY: 0.5218,
      radiusX: 0.085,
      radiusY: 0.055,
    },
    body: HEAD_BODY,
  },
};

type Size = { width: number; height: number };

type Candidate = {
  name: string;
  size: Size;
  options: Partial<RotoscopeOptions>;
};

type Scores = {
  name: string;
  width: number;
  height: number;
  ms: number;
  eyeSsim: number;
  eyeMae: number;
  eyeBasins: number;
  leftEyeBasins: number;
  rightEyeBasins: number;
  headBasins: number;
  backgroundBasins: number;
  markerCount: number;
  tierCounts: RotoscopeResult["tierCounts"];
  shatter: number;
  bgInflation: number;
  score: number;
  skipped?: string;
};

const rgbaKey = (pixels: Uint8ClampedArray, index: number): number =>
  (pixels[index] << 16) | (pixels[index + 1] << 8) | pixels[index + 2];

const inEllipse = (
  nx: number,
  ny: number,
  cx: number,
  cy: number,
  rx: number,
  ry: number,
): boolean => {
  const dx = (nx - cx) / rx;
  const dy = (ny - cy) / ry;
  return dx * dx + dy * dy <= 1;
};

const uniqueColors = (
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  keep: (x: number, y: number) => boolean,
): number => {
  const colors = new Set<number>();
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (!keep(x, y)) continue;
      colors.add(rgbaKey(pixels, (y * width + x) * 4));
    }
  }
  return colors.size;
};

const cropBox = (width: number, height: number) => {
  const left = Math.max(0, Math.floor((LEFT_EYE.x - EYE_PAD * LEFT_EYE.rx) * width));
  const right = Math.min(
    width,
    Math.ceil((RIGHT_EYE.x + EYE_PAD * RIGHT_EYE.rx) * width),
  );
  const top = Math.max(
    0,
    Math.floor((Math.min(LEFT_EYE.y, RIGHT_EYE.y) - EYE_PAD * LEFT_EYE.ry) * height),
  );
  const bottom = Math.min(
    height,
    Math.ceil((Math.max(LEFT_EYE.y, RIGHT_EYE.y) + EYE_PAD * RIGHT_EYE.ry) * height),
  );
  return { left, top, width: right - left, height: bottom - top };
};

const extractGray = (
  pixels: Uint8ClampedArray,
  srcWidth: number,
  box: ReturnType<typeof cropBox>,
): Float64Array => {
  const gray = new Float64Array(box.width * box.height);
  for (let y = 0; y < box.height; y += 1) {
    for (let x = 0; x < box.width; x += 1) {
      const offset = ((box.top + y) * srcWidth + (box.left + x)) * 4;
      gray[y * box.width + x] =
        0.2126 * pixels[offset] + 0.7152 * pixels[offset + 1] + 0.0722 * pixels[offset + 2];
    }
  }
  return gray;
};

const ssim = (a: Float64Array, b: Float64Array): number => {
  const n = a.length;
  let sumA = 0;
  let sumB = 0;
  for (let i = 0; i < n; i += 1) {
    sumA += a[i];
    sumB += b[i];
  }
  const muA = sumA / n;
  const muB = sumB / n;
  let varA = 0;
  let varB = 0;
  let cov = 0;
  for (let i = 0; i < n; i += 1) {
    const da = a[i] - muA;
    const db = b[i] - muB;
    varA += da * da;
    varB += db * db;
    cov += da * db;
  }
  varA /= n - 1;
  varB /= n - 1;
  cov /= n - 1;
  const c1 = (0.01 * 255) ** 2;
  const c2 = (0.03 * 255) ** 2;
  return (
    ((2 * muA * muB + c1) * (2 * cov + c2)) /
    ((muA * muA + muB * muB + c1) * (varA + varB + c2))
  );
};

const mae = (a: Float64Array, b: Float64Array): number => {
  let sum = 0;
  for (let i = 0; i < a.length; i += 1) sum += Math.abs(a[i] - b[i]);
  return sum / a.length;
};

const loadSource = async (size: Size): Promise<Uint8ClampedArray> => {
  const { data, info } = await sharp(SOURCE)
    .resize(size.width, size.height, { fit: "fill" })
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  if (info.width !== size.width || info.height !== size.height) {
    throw new Error(`unexpected resize ${info.width}x${info.height}`);
  }
  return new Uint8ClampedArray(
    data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength),
  );
};

const writeRgbPng = async (
  file: string,
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  box?: ReturnType<typeof cropBox>,
): Promise<void> => {
  const region = box ?? { left: 0, top: 0, width, height };
  const rgb = Buffer.alloc(region.width * region.height * 3);
  for (let y = 0; y < region.height; y += 1) {
    for (let x = 0; x < region.width; x += 1) {
      const src = ((region.top + y) * width + (region.left + x)) * 4;
      const dst = (y * region.width + x) * 3;
      rgb[dst] = pixels[src];
      rgb[dst + 1] = pixels[src + 1];
      rgb[dst + 2] = pixels[src + 2];
    }
  }
  await sharp(rgb, {
    raw: { width: region.width, height: region.height, channels: 3 },
  })
    .png()
    .toFile(file);
};

const evaluate = async (
  candidate: Candidate,
  sourceBySize: Map<string, Uint8ClampedArray>,
  baseline: Scores | null,
): Promise<Scores> => {
  const { width, height } = candidate.size;
  if (width * height > MAX_ROTOSCOPE_PIXELS) {
    return {
      name: candidate.name,
      width,
      height,
      ms: 0,
      eyeSsim: 0,
      eyeMae: 0,
      eyeBasins: 0,
      leftEyeBasins: 0,
      rightEyeBasins: 0,
      headBasins: 0,
      backgroundBasins: 0,
      markerCount: 0,
      tierCounts: { face: 0, body: 0, background: 0 },
      shatter: 0,
      bgInflation: 0,
      score: -Infinity,
      skipped: `exceeds MAX_ROTOSCOPE_PIXELS (${width * height} > ${MAX_ROTOSCOPE_PIXELS})`,
    };
  }
  const key = `${width}x${height}`;
  let source = sourceBySize.get(key);
  if (!source) {
    source = await loadSource(candidate.size);
    sourceBySize.set(key, source);
  }
  const started = performance.now();
  const painted = runRotoscope(source, width, height, candidate.options);
  const ms = performance.now() - started;
  const box = cropBox(width, height);
  const eyeSsim = ssim(extractGray(painted.pixels, width, box), extractGray(source, width, box));
  const eyeMae = mae(extractGray(painted.pixels, width, box), extractGray(source, width, box));
  const eyeBasins = uniqueColors(
    painted.pixels,
    width,
    height,
    (x, y) =>
      x >= box.left && x < box.left + box.width && y >= box.top && y < box.top + box.height,
  );
  const leftEyeBasins = uniqueColors(painted.pixels, width, height, (x, y) =>
    inEllipse((x + 0.5) / width, (y + 0.5) / height, LEFT_EYE.x, LEFT_EYE.y, LEFT_EYE.rx, LEFT_EYE.ry),
  );
  const rightEyeBasins = uniqueColors(painted.pixels, width, height, (x, y) =>
    inEllipse(
      (x + 0.5) / width,
      (y + 0.5) / height,
      RIGHT_EYE.x,
      RIGHT_EYE.y,
      RIGHT_EYE.rx,
      RIGHT_EYE.ry,
    ),
  );
  const headBasins = uniqueColors(painted.pixels, width, height, (x, y) =>
    inEllipse(
      (x + 0.5) / width,
      (y + 0.5) / height,
      HEAD_FACE.centerX,
      HEAD_FACE.centerY,
      HEAD_FACE.radiusX,
      HEAD_FACE.radiusY,
    ),
  );
  const backgroundBasins = uniqueColors(painted.pixels, width, height, (x, y) => {
    const nx = (x + 0.5) / width;
    const ny = (y + 0.5) / height;
    if (
      inEllipse(nx, ny, HEAD_FACE.centerX, HEAD_FACE.centerY, HEAD_FACE.radiusX, HEAD_FACE.radiusY)
    ) {
      return false;
    }
    const body = BASELINE_BODY;
    let inside = false;
    for (let i = 0, j = body.length - 1; i < body.length; j = i, i += 1) {
      const [xi, yi] = body[i];
      const [xj, yj] = body[j];
      const intersects =
        yi > ny !== yj > ny &&
        nx < ((xj - xi) * (ny - yi)) / (yj - yi || Number.EPSILON) + xi;
      if (intersects) inside = !inside;
    }
    return !inside;
  });
  const shatter = baseline
    ? Math.max(0, headBasins / Math.max(1, baseline.headBasins) - 1.25)
    : 0;
  const bgInflation = baseline
    ? Math.max(0, backgroundBasins / Math.max(1, baseline.backgroundBasins) - 1.2)
    : 0;
  const score = eyeSsim - 0.25 * shatter - 0.2 * bgInflation;
  await writeRgbPng(
    path.join(OUT_DIR, `${candidate.name}-full.png`),
    painted.pixels,
    width,
    height,
  );
  await writeRgbPng(
    path.join(OUT_DIR, `${candidate.name}-eyes.png`),
    painted.pixels,
    width,
    height,
    box,
  );
  return {
    name: candidate.name,
    width,
    height,
    ms,
    eyeSsim,
    eyeMae,
    eyeBasins,
    leftEyeBasins,
    rightEyeBasins,
    headBasins,
    backgroundBasins,
    markerCount: painted.markerCount,
    tierCounts: painted.tierCounts,
    shatter,
    bgInflation,
    score,
  };
};

const periocular = (
  name: string,
  radiusX: number,
  radiusY: number,
  extras: Partial<RotoscopeOptions> = {},
): Candidate => {
  const quotas = extras.quotas ?? { face: 0.3, body: 0.64, background: 0.06 };
  return {
    name,
    size: { width: 960, height: 720 },
    options: {
      blurRadius: extras.blurRadius ?? 3,
      markerBudget: extras.markerBudget ?? 1600,
      quotas,
      spacing: extras.spacing ?? { face: 1, body: 4, background: 8 },
      focus: {
        face: { ...PERIOCULAR_CENTER, radiusX, radiusY },
        body: HEAD_BODY,
      },
    },
  };
};

const logRow = (row: Scores): void => {
  if (row.skipped) {
    process.stdout.write(`${row.name} SKIP ${row.skipped}\n`);
    return;
  }
  process.stdout.write(
    [
      row.name.padEnd(28),
      `ssim=${row.eyeSsim.toFixed(4)}`,
      `mae=${row.eyeMae.toFixed(2)}`,
      `eyes=${row.leftEyeBasins}/${row.rightEyeBasins}`,
      `head=${row.headBasins}`,
      `bg=${row.backgroundBasins}`,
      `faceN=${row.tierCounts.face}`,
      `score=${row.score.toFixed(4)}`,
      `${row.ms.toFixed(0)}ms`,
    ].join("  ") + "\n",
  );
};

const bestOf = (rows: Scores[]): Scores =>
  rows
    .filter((row) => !row.skipped)
    .reduce((best, row) => (row.score > best.score ? row : best));

const main = async (): Promise<void> => {
  await fs.mkdir(OUT_DIR, { recursive: true });
  const sourceBySize = new Map<string, Uint8ClampedArray>();
  const all: Scores[] = [];
  const byName = new Map<string, Candidate>();

  const run = async (candidate: Candidate, baseline: Scores | null): Promise<Scores> => {
    byName.set(candidate.name, candidate);
    const row = await evaluate(candidate, sourceBySize, baseline);
    all.push(row);
    logRow(row);
    return row;
  };

  const baseline = await run(
    {
      name: "00-baseline",
      size: { width: 960, height: 720 },
      options: BASELINE_OPTIONS,
    },
    null,
  );

  for (const size of [
    { width: 1280, height: 960 },
    { width: 1440, height: 1080 },
  ]) {
    await run(
      {
        name: `skip-${size.width}x${size.height}`,
        size,
        options: BASELINE_OPTIONS,
      },
      baseline,
    );
  }

  // Coordinate descent vs current periocular production. Keep spacing.face = 1.
  const ellipseRows = [
    await run(periocular("01-ellipse-075-050", 0.075, 0.05), baseline),
    await run(periocular("01-ellipse-080-050", 0.08, 0.05), baseline),
    await run(periocular("01-ellipse-085-050", 0.085, 0.05), baseline),
    await run(periocular("01-ellipse-085-055", 0.085, 0.055), baseline),
    await run(periocular("01-ellipse-090-055", 0.09, 0.055), baseline),
    await run(periocular("01-ellipse-090-060", 0.09, 0.06), baseline),
    await run(periocular("01-ellipse-095-055", 0.095, 0.055), baseline),
    await run(periocular("01-ellipse-080-060", 0.08, 0.06), baseline),
  ];
  let winner = bestOf([baseline, ...ellipseRows]);
  let best = byName.get(winner.name) as Candidate;
  const radiusX = best.options.focus?.face.radiusX ?? 0.085;
  const radiusY = best.options.focus?.face.radiusY ?? 0.055;

  const takeIfBetter = (row: Scores): void => {
    if (row.skipped) return;
    if (row.score > winner.score + 1e-6) {
      winner = row;
      best = byName.get(row.name) as Candidate;
    } else if (
      Math.abs(row.score - winner.score) <= 1e-6 &&
      row.width * row.height < winner.width * winner.height
    ) {
      winner = row;
      best = byName.get(row.name) as Candidate;
    }
  };

  for (const face of [0.24, 0.3, 0.36]) {
    const background = best.options.quotas?.background ?? 0.06;
    takeIfBetter(
      await run(
        periocular(`02-quota-f${face.toFixed(2)}`, radiusX, radiusY, {
          ...best.options,
          quotas: { face, body: 1 - face - background, background },
        }),
        baseline,
      ),
    );
  }

  const face = best.options.quotas?.face ?? 0.3;
  for (const background of [0.05, 0.06, 0.08]) {
    takeIfBetter(
      await run(
        periocular(`03-bg-${background.toFixed(2)}`, radiusX, radiusY, {
          ...best.options,
          quotas: { face, body: 1 - face - background, background },
        }),
        baseline,
      ),
    );
  }

  for (const blurRadius of [2, 3, 4]) {
    takeIfBetter(
      await run(
        periocular(`04-blur-${blurRadius}`, radiusX, radiusY, {
          ...best.options,
          blurRadius,
        }),
        baseline,
      ),
    );
  }

  for (const markerBudget of [1600, 1800]) {
    const row = await run(
      periocular(`05-budget-${markerBudget}`, radiusX, radiusY, {
        ...best.options,
        markerBudget,
      }),
      baseline,
    );
    if (markerBudget === 1800 && row.shatter > 0) {
      process.stdout.write(
        `${row.name} REJECT head basins exploded vs baseline (shatter=${row.shatter.toFixed(3)})\n`,
      );
      continue;
    }
    takeIfBetter(row);
  }

  for (const body of [3, 4]) {
    takeIfBetter(
      await run(
        periocular(`06-body-space-${body}`, radiusX, radiusY, {
          ...best.options,
          spacing: { face: 1, body, background: best.options.spacing?.background ?? 8 },
        }),
        baseline,
      ),
    );
  }

  const locked = best.options;
  for (const [name, nextRx, nextRy] of [
    ["07-ellipse2-075-050", 0.075, 0.05],
    ["07-ellipse2-080-050", 0.08, 0.05],
    ["07-ellipse2-085-055", 0.085, 0.055],
    ["07-ellipse2-090-055", 0.09, 0.055],
    ["07-ellipse2-090-060", 0.09, 0.06],
    ["07-ellipse2-095-055", 0.095, 0.055],
  ] as const) {
    takeIfBetter(await run(periocular(name, nextRx, nextRy, { ...locked }), baseline));
  }

  const irisBasins = (row: Scores): number => row.leftEyeBasins + row.rightEyeBasins;
  const irisImproved = irisBasins(winner) > irisBasins(baseline);

  const ranked = all.filter((row) => !row.skipped).sort((a, b) => b.score - a.score);
  const top = ranked.slice(0, 8);
  await fs.writeFile(
    path.join(OUT_DIR, "scores.json"),
    `${JSON.stringify(
      {
        baseline,
        winner,
        options: best.options,
        irisImproved,
        baselineIrisBasins: irisBasins(baseline),
        winnerIrisBasins: irisBasins(winner),
        top,
        all,
      },
      null,
      2,
    )}\n`,
  );
  process.stdout.write(
    `\nWINNER ${winner.name} score=${winner.score.toFixed(4)} ssim=${winner.eyeSsim.toFixed(4)} vs baseline ssim=${baseline.eyeSsim.toFixed(4)}\n`,
  );
  process.stdout.write(
    `iris basins winner=${irisBasins(winner)} baseline=${irisBasins(baseline)} improved=${irisImproved}\n`,
  );
  if (!irisImproved) {
    process.stdout.write(
      "STOP: 3-tier remap cannot put more iris markers than the current periocular baseline.\n",
    );
  }
  process.stdout.write(`${JSON.stringify(best.options, null, 2)}\n`);
  process.stdout.write(`stills ${OUT_DIR}\n`);
};

void main();
