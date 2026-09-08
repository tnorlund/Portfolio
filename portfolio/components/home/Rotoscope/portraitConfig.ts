import type { RotoscopeOptions } from "./algorithm";

/** Full-viewport 4:3 pass. 960×720 matches the authored portrait files. */
export const PORTRAIT_PROCESSING_SIZE = { width: 960, height: 720 } as const;

/**
 * Homepage-only geometry for `/rotoscope-portrait.*` after its full-frame 4:3
 * resize. The face ellipse is periocular (both Vision eyes plus lid/brow) so
 * the face quota lands in the irises instead of the whole skull. The rest of
 * the head is body. Engine defaults stay in `algorithm.ts`.
 */
export const PORTRAIT_ROTOSCOPE_OPTIONS: Partial<RotoscopeOptions> = {
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
    body: [
      [0.26, 0.3],
      [0.4, 0.26],
      [0.54, 0.3],
      [0.58, 0.52],
      [0.75, 0.82],
      [0.8, 1],
      [0, 1],
      [0, 0.84],
      [0.22, 0.52],
    ],
  },
};

export const PORTRAIT_SOURCES = {
  basins: "/rotoscope-basins.webp",
  avif: "/rotoscope-portrait.avif",
  webp: "/rotoscope-portrait.webp",
  fallback: "/rotoscope-portrait.jpg",
} as const;
