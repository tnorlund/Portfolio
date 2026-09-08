import path from "path";
import sharp from "sharp";
import { runRotoscope } from "../components/home/Rotoscope/algorithm";
import {
  PORTRAIT_PROCESSING_SIZE,
  PORTRAIT_ROTOSCOPE_OPTIONS,
} from "../components/home/Rotoscope/portraitConfig";

/**
 * Rebuild public/rotoscope-basins.webp from the homepage portrait pass.
 *
 *   npx tsx scripts/generate-rotoscope-basins.ts
 *
 * Outlines every connected flat-color basin in the 960×720 homepage result so
 * the no-JS fallback matches the live Replay crop and focus.
 */
const ROOT = path.join(__dirname, "..");
const SOURCE = path.join(ROOT, "public/rotoscope-portrait.jpg");
const OUTPUT = path.join(ROOT, "public/rotoscope-basins.webp");
const FILL = 221;
const STROKE = 0;

const main = async (): Promise<void> => {
  const { width, height } = PORTRAIT_PROCESSING_SIZE;
  const { data, info } = await sharp(SOURCE)
    .resize(width, height, { fit: "fill" })
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  if (info.width !== width || info.height !== height) {
    throw new Error(`unexpected resize ${info.width}x${info.height}`);
  }
  const source = new Uint8ClampedArray(
    data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength),
  );
  const painted = runRotoscope(source, width, height, PORTRAIT_ROTOSCOPE_OPTIONS);
  const outline = Buffer.alloc(width * height, FILL);
  const colorAt = (index: number): number => {
    const offset = index * 4;
    return (
      (painted.pixels[offset] << 16) |
      (painted.pixels[offset + 1] << 8) |
      painted.pixels[offset + 2]
    );
  };
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = y * width + x;
      const color = colorAt(index);
      const edge =
        (x > 0 && colorAt(index - 1) !== color) ||
        (x + 1 < width && colorAt(index + 1) !== color) ||
        (y > 0 && colorAt(index - width) !== color) ||
        (y + 1 < height && colorAt(index + width) !== color);
      if (edge) outline[index] = STROKE;
    }
  }
  await sharp(outline, { raw: { width, height, channels: 1 } })
    .webp({ lossless: true })
    .toFile(OUTPUT);
  process.stdout.write(`${OUTPUT} ${width}x${height}\n`);
};

void main();
