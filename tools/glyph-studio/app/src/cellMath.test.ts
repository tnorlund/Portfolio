import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  capHeight,
  advanceRatio,
  cellWidth,
  scaledGlyph,
  renderHeight,
  pyRound,
  type Atlas,
} from "./cellMath";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixturePath = path.resolve(
  here,
  "../../fixtures/cellmath_cases.json",
);

interface Case {
  ch: string;
  cap_px: number;
  thin: number;
  condense: number;
  cap_h: number;
  advance_ratio: number;
  cell_w: number;
  scaled_h: number;
  scaled_w: number;
  final_w: number;
  off_px: number;
  bitmap_sha1?: string;
}
interface Fixture {
  atlas: Record<string, { w: number; h: number; off: number; rows: string[] }>;
  cases: Case[];
}

const fixture: Fixture = JSON.parse(readFileSync(fixturePath, "utf-8"));
const atlas: Atlas = {};
for (const [cp, g] of Object.entries(fixture.atlas)) {
  atlas[Number(cp)] = { w: g.w, h: g.h, off: g.off, rows: g.rows };
}

describe("cellMath parity with glyphstudio.cellmath fixtures", () => {
  const capH = capHeight(atlas);
  const advR = advanceRatio(atlas, capH);

  it("capHeight matches fixture cap_h", () => {
    for (const c of fixture.cases) expect(capH).toBe(c.cap_h);
  });

  it("advanceRatio matches fixture advance_ratio", () => {
    for (const c of fixture.cases) expect(advR).toBeCloseTo(c.advance_ratio, 12);
  });

  it("cellWidth matches every case", () => {
    for (const c of fixture.cases) {
      const cw = cellWidth(c.cap_px, advR, c.condense);
      expect(cw).toBeCloseTo(c.cell_w, 9);
    }
  });

  it("scaled dims / final_w / off_px match every case", () => {
    for (const c of fixture.cases) {
      const g = atlas[c.ch.codePointAt(0)!];
      expect(g, `atlas missing ${c.ch}`).toBeTruthy();
      const cw = cellWidth(c.cap_px, advR, c.condense);
      const s = scaledGlyph(g, c.cap_px, capH, cw, c.condense);
      const ctx = `${c.ch} cap${c.cap_px} cond${c.condense}`;
      expect(s.scaled_h, `scaled_h ${ctx}`).toBe(c.scaled_h);
      expect(s.scaled_w, `scaled_w ${ctx}`).toBe(c.scaled_w);
      expect(s.final_w, `final_w ${ctx}`).toBe(c.final_w);
      expect(s.off_px, `off_px ${ctx}`).toBe(c.off_px);
    }
  });

  it("renderHeight forces caps/digits/$ to capPx, leaves lowercase unforced", () => {
    const capPx = 16;
    const scale = capPx / capH;
    for (const ch of ["A", "5", "$"]) {
      const g = atlas[ch.codePointAt(0)!];
      expect(renderHeight(ch, g, capPx, capH), `${ch} forced`).toBe(capPx);
    }
    for (const ch of ["o", "p"]) {
      const g = atlas[ch.codePointAt(0)!];
      const unforced = Math.max(1, pyRound(g.h * scale));
      expect(renderHeight(ch, g, capPx, capH), `${ch} unforced`).toBe(unforced);
      // sanity: these lowercase glyphs are shorter than a full cap
      expect(unforced).toBeLessThan(capPx);
    }
  });
});
