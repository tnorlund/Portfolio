import React, { useState } from "react";
import styles from "../../styles/Rotoscope.module.css";

type StageKind = "difference" | "features" | "watershed" | "color";

const BASIN_PATHS = [
  "M0 0H74L70 39L43 55L0 49Z",
  "M74 0H145L151 34L119 59L70 39Z",
  "M145 0H220V47L180 61L151 34Z",
  "M0 49L43 55L70 88L50 118L0 110Z",
  "M43 55L70 39L119 59L111 103L70 88Z",
  "M119 59L151 34L180 61L174 102L137 116L111 103Z",
  "M180 61L220 47V104L174 102Z",
  "M0 110L50 118L78 150H0Z",
  "M50 118L70 88L111 103L118 150H78Z",
  "M111 103L137 116L158 150H118Z",
  "M137 116L174 102L220 104V150H158Z",
] as const;

const BASIN_COLORS = [
  "#96a9a4",
  "#e5b952",
  "#c7c3a9",
  "#527a86",
  "#344454",
  "#718f8d",
  "#e28746",
  "#aeb6b5",
  "#65727a",
  "#d39a59",
  "#9caa91",
] as const;

const FILM_HOLES = [14, 42, 70, 98, 126, 154] as const;

const FACE_MARKERS = [
  [104, 25], [116, 23], [128, 26], [139, 32], [147, 41], [151, 52],
  [150, 64], [145, 73], [151, 82], [146, 92], [135, 98], [124, 99],
  [112, 96], [102, 88], [96, 77], [93, 65], [94, 52], [98, 39],
  [111, 48], [125, 51], [136, 58], [126, 70], [116, 73], [106, 65],
] as const;

const BODY_MARKERS = [
  [94, 104], [83, 109], [73, 116], [65, 125], [57, 136], [78, 132],
  [99, 122], [111, 113], [125, 119], [139, 129], [153, 139],
] as const;

const BACKGROUND_MARKERS = [
  [20, 24], [45, 51], [27, 91], [50, 146], [180, 25], [196, 73],
  [183, 116], [207, 143], [169, 53], [36, 121],
] as const;

function FilmFrame({ kind }: { kind: StageKind }) {
  const colorStage = kind === "color";
  const basinStage = kind === "watershed" || colorStage;

  return (
    <svg
      className={styles.filmFrame}
      viewBox="0 0 244 180"
      role="img"
      aria-label={`${kind} stage illustration`}
    >
      <rect width="244" height="180" rx="3" fill="#050505" />
      {FILM_HOLES.map((y) => (
        <React.Fragment key={y}>
          <rect x="6" y={y} width="10" height="12" rx="2" fill="#fff" />
          <rect x="228" y={y} width="10" height="12" rx="2" fill="#fff" />
        </React.Fragment>
      ))}
      <g transform="translate(12 0) scale(1 1.2)">
        <rect width="220" height="150" fill={basinStage ? "#d7d7d7" : "#101010"} />
        {basinStage
          ? BASIN_PATHS.map((path, index) => (
              <path
                key={path}
                d={path}
                fill={colorStage ? BASIN_COLORS[index] : `rgb(${95 + index * 8}, ${95 + index * 8}, ${95 + index * 8})`}
                stroke="#fff"
                strokeWidth="1.2"
              />
            ))
          : null}
        <path
          d="M92 145C92 127 95 110 103 101C106 97 111 94 116 91C108 83 105 71 106 57C107 37 119 23 136 21C153 19 167 30 169 48C170 55 168 61 170 65C172 69 179 73 178 77C177 80 172 82 169 83C167 92 160 99 151 102C154 111 156 128 156 145Z"
          fill={colorStage ? "#344454" : basinStage ? "#4f4f4f" : "none"}
          stroke="#fff"
          strokeWidth={kind === "difference" ? "2.4" : "1.6"}
        />
        {kind === "difference" ? (
          <>
            <path d="M105 54L97 45M165 52L176 42M112 99L99 111M155 102L171 115" stroke="#777" />
            <path d="M30 25L44 30M40 92L24 101M190 65L207 58M181 126L199 135" stroke="#4a4a4a" />
          </>
        ) : null}
        {kind === "features" ? (
          <g>
            {[...FACE_MARKERS, ...BODY_MARKERS].map(([cx, cy], index) => (
              <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={index % 3 === 0 ? 2.1 : 1.5} fill="#fff" />
            ))}
            {BACKGROUND_MARKERS.map(([cx, cy]) => (
              <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1.5" fill="#858585" />
            ))}
          </g>
        ) : null}
      </g>
    </svg>
  );
}

function Arrow() {
  return (
    <svg className={styles.arrow} viewBox="0 0 34 24" aria-hidden="true">
      <path d="M2 12H29M21 4L29 12L21 20" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PipelineOverview() {
  const stages: ReadonlyArray<{ label: string; kind: StageKind }> = [
    { label: "Difference", kind: "difference" },
    { label: "Features", kind: "features" },
    { label: "Watershed", kind: "watershed" },
    { label: "Average color", kind: "color" },
  ];

  return (
    <div className={styles.pipelineScroller} aria-label="The four rotoscoping stages">
      <div className={styles.pipeline}>
        {stages.map((stage, index) => (
          <React.Fragment key={stage.kind}>
            <figure className={styles.stage}>
              <figcaption>{stage.label}</figcaption>
              <FilmFrame kind={stage.kind} />
            </figure>
            {index < stages.length - 1 ? <Arrow /> : null}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

function PortraitArt({ blurred = false }: { blurred?: boolean }) {
  return (
    <svg className={styles.processArt} viewBox="0 0 220 180" role="img" aria-label={blurred ? "Blurred portrait outline" : "Portrait outline"}>
      <rect width="220" height="180" fill="#fff" />
      <g transform="scale(1 1.2)" className={blurred ? styles.blurred : undefined}>
        <path
          d="M65 150C68 128 78 112 96 103C88 92 84 80 84 65C84 40 101 22 124 20C148 18 166 34 168 57C168 65 166 70 169 74C173 78 180 82 178 87C176 91 171 92 167 93C164 103 157 110 146 113C151 124 154 136 155 150"
          fill="none"
          stroke="#151515"
          strokeWidth="2"
        />
        <path d="M106 62C117 55 132 55 143 61M139 75L145 77M136 93C143 95 149 94 154 90" fill="none" stroke="#151515" strokeWidth="1.6" />
      </g>
    </svg>
  );
}

function ProcessPanel({ label, kind }: { label: string; kind: "portrait" | "blur" | "difference" }) {
  return (
    <figure className={styles.processPanel}>
      <figcaption>{label}</figcaption>
      <div className={styles.miniFilm}>
        {kind === "portrait" ? <PortraitArt /> : null}
        {kind === "blur" ? <PortraitArt blurred /> : null}
        {kind === "difference" ? <FilmFrame kind="difference" /> : null}
      </div>
    </figure>
  );
}

function DifferenceFigure() {
  return (
    <div className={styles.equation} aria-label="Portrait minus blurred copy equals difference">
      <ProcessPanel label="Portrait" kind="portrait" />
      <span className={styles.operator} aria-hidden="true">−</span>
      <ProcessPanel label="Blurred copy" kind="blur" />
      <span className={styles.operator} aria-hidden="true">=</span>
      <ProcessPanel label="Difference" kind="difference" />
    </div>
  );
}

function MarkerFigure() {
  return (
    <figure className={styles.markerFigure}>
      <svg className={styles.markerArt} viewBox="0 0 230 170" role="img" aria-label="Feature markers distributed across face, body, and background">
        <path d="M61 166C64 142 77 121 95 108C88 96 84 83 85 66C86 40 102 22 125 20C149 18 167 34 169 57C170 66 166 72 170 77C174 81 181 84 179 89C177 93 171 94 167 95C164 105 156 113 145 116C152 128 157 145 159 166" fill="none" stroke="#b7b7b7" strokeWidth="1" />
        {FACE_MARKERS.map(([cx, cy]) => <circle key={`f-${cx}-${cy}`} cx={cx} cy={cy} r="2.3" fill="#1e88e5" />)}
        {BODY_MARKERS.map(([cx, cy]) => <circle key={`b-${cx}-${cy}`} cx={cx} cy={cy} r="2.3" fill="#fb8c00" />)}
        {BACKGROUND_MARKERS.map(([cx, cy]) => <circle key={`g-${cx}-${cy}`} cx={cx} cy={cy} r="2.1" fill="#9d9d9d" />)}
      </svg>
      <figcaption className={styles.markerLegend}>
        <span><i className={styles.faceDot} />Face 50%</span>
        <span><i className={styles.bodyDot} />Body 30%</span>
        <span><i className={styles.backgroundDot} />Background 20%</span>
      </figcaption>
    </figure>
  );
}

function WatershedArt({ replayKey }: { replayKey: number }) {
  const seeds = [
    [34, 29, "#1e88e5"], [102, 28, "#8d8d8d"], [174, 31, "#00897b"],
    [54, 81, "#fb8c00"], [121, 79, "#1e88e5"], [192, 84, "#f2b716"],
    [31, 127, "#8d8d8d"], [101, 126, "#00897b"], [178, 126, "#d86b13"],
  ] as const;

  return (
    <svg key={replayKey} className={styles.watershedArt} viewBox="0 0 220 150" role="img" aria-label="Markers flooding outward into catchment basins">
      <rect width="220" height="150" fill="#fff" stroke="#8b8b8b" />
      {BASIN_PATHS.map((path, index) => (
        <path
          key={path}
          className={styles.floodRegion}
          style={{ "--delay": `${index * 70}ms` } as React.CSSProperties}
          d={path}
          fill="#fff"
          stroke="#777"
          strokeWidth="1"
        />
      ))}
      {seeds.map(([cx, cy, fill]) => (
        <g key={`${cx}-${cy}`}>
          <circle className={styles.seedWave} cx={cx} cy={cy} r="10" fill="none" stroke={fill} />
          <circle cx={cx} cy={cy} r="3.2" fill={fill} />
        </g>
      ))}
    </svg>
  );
}

function WatershedFigure() {
  const [replayKey, setReplayKey] = useState(0);

  return (
    <figure className={styles.watershedFigure}>
      <WatershedArt replayKey={replayKey} />
      <figcaption>
        <button className={styles.replayButton} type="button" onClick={() => setReplayKey((key) => key + 1)}>
          Replay the flood
        </button>
      </figcaption>
    </figure>
  );
}

function RegionMap({ painted }: { painted: boolean }) {
  return (
    <svg className={styles.regionMap} viewBox="0 0 220 150" role="img" aria-label={painted ? "Flat painted regions" : "Textured pixels grouped into regions"}>
      <defs>
        <pattern id={painted ? "painted-dots" : "pixel-dots"} width="6" height="6" patternUnits="userSpaceOnUse">
          <rect width="6" height="6" fill="#555" />
          <rect x="0" y="0" width="2" height="2" fill="#898989" />
          <rect x="3" y="2" width="2" height="2" fill="#2f2f2f" />
          <rect x="1" y="4" width="2" height="2" fill="#707070" />
        </pattern>
      </defs>
      {BASIN_PATHS.map((path, index) => (
        <path key={path} d={path} fill={painted ? BASIN_COLORS[index] : "url(#pixel-dots)"} stroke="#fff" strokeWidth="1.4" />
      ))}
    </svg>
  );
}

function PaintFigure() {
  return (
    <div className={styles.paintFigure}>
      <figure>
        <figcaption>Pixels</figcaption>
        <RegionMap painted={false} />
      </figure>
      <Arrow />
      <figure>
        <figcaption>Painted regions</figcaption>
        <RegionMap painted />
      </figure>
    </div>
  );
}

export default function RotoscopeExplainer() {
  return (
    <article>
      <section className={styles.hero}>
        <h1>How the rotoscope works</h1>
        <p>
          Rotoscoping usually means tracing a subject one frame at a time. This
          version starts with features instead: find the places worth preserving,
          grow a region around each one, then paint every region with one average
          color.
        </p>
        <PipelineOverview />
      </section>

      <section className={styles.section}>
        <h2>Start with what changed</h2>
        <p>
          The original algorithm compares a frame with a clean background. On this
          page there is only one portrait, so a blurred copy stands in for that
          second frame. Subtract the two and the quiet parts disappear. Edges and
          small details stay bright.
        </p>
        <DifferenceFigure />
      </section>

      <section className={styles.section}>
        <h2>Spend detail where it matters</h2>
        <p>
          Shi–Tomasi scores the corners and texture in the difference image. The
          strongest points become markers. Half go to the face, three in ten to the
          body, and the rest to the background, so a busy wall cannot steal all the
          detail. Those 50/30/20 shares are the paper and engine defaults. The
          homepage portrait overrides them to 55/30/15 so the face keeps a little
          more of the budget.
        </p>
        <MarkerFigure />
      </section>

      <section className={styles.section}>
        <h2>Let the regions grow</h2>
        <p>
          Imagine dropping every marker onto a landscape made from image edges.
          Each marker floods outward through easy ground and slows at a strong edge.
          When every pixel has been claimed, the image is divided into catchment
          basins.
        </p>
        <WatershedFigure />
      </section>

      <section className={styles.section}>
        <h2>Replace pixels with paint</h2>
        <p>
          The final step forgets the tiny differences inside each basin. It averages
          the source colors in that region and fills the whole shape with one flat
          color. Hundreds of thousands of pixels become a few hundred painted
          pieces.
        </p>
        <PaintFigure />
      </section>

      <section className={`${styles.section} ${styles.technicalNote}`}>
        <h2>What changed for the browser?</h2>
        <p>
          The 2017 version used a clean background frame. The browser demo uses a
          blurred copy of one portrait instead. The stage order stays the same; two
          small kernels are simplified so it can run quickly at display size.
        </p>
        <nav className={styles.links} aria-label="Rotoscope references">
          <a href="https://doi.org/10.1109/ACSSC.2017.8335175" target="_blank" rel="noreferrer">Read the paper</a>
          <a href="https://github.com/tnorlund/BestFeatureRotoscope" target="_blank" rel="noreferrer">View the source</a>
        </nav>
      </section>
    </article>
  );
}
