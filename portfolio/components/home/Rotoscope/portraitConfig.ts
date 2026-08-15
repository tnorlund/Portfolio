import type { RotoscopeOptions } from "./algorithm";

/** Display-derived processing size: 480x360 is enough for a 240px DPR2 hero. */
export const PORTRAIT_PROCESSING_SIZE = { width: 480, height: 360 } as const;

/**
 * Authored geometry for `/rotoscope-portrait.*` after its full-frame 4:3 resize.
 * Explicit quotas keep the busy restaurant background from consuming the
 * feature budget: face first, then torso, then scene context.
 */
export const PORTRAIT_ROTOSCOPE_OPTIONS: Partial<RotoscopeOptions> = {
  blurRadius: 9,
  markerBudget: 720,
  quotas: { face: 0.55, body: 0.3, background: 0.15 },
  spacing: { face: 2, body: 4, background: 8 },
  focus: {
    face: {
      centerX: 0.4,
      centerY: 0.56,
      radiusX: 0.14,
      radiusY: 0.27,
    },
    body: [
      [0.31, 0.67],
      [0.53, 0.66],
      [0.75, 0.82],
      [0.8, 1],
      [0, 1],
      [0, 0.84],
      [0.26, 0.72],
    ],
  },
};

export const PORTRAIT_SOURCES = {
  avif: "/rotoscope-portrait.avif",
  webp: "/rotoscope-portrait.webp",
  fallback: "/rotoscope-portrait.jpg",
} as const;
