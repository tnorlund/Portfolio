import React, { useCallback, useEffect, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import styles from "../../styles/Rotoscope.module.css";

/**
 * Every figure on this page is a real still from the homepage portrait pass:
 * public/rotoscope-portrait.jpg resized to 960x720 and run through the engine
 * with the homepage overrides (blur 6, 1600 markers, 70/22/8 quotas). The
 * stills are checked in next to the portrait so the article needs no runtime
 * engine; regenerate them by re-running that pass whenever the engine or the
 * portrait changes.
 */
const STILL_WIDTH = 960;
const STILL_HEIGHT = 720;

const STILLS = {
  blurred: "/rotoscope-blurred.webp",
  difference: "/rotoscope-difference.webp",
  markers: "/rotoscope-markers.webp",
  basins: "/rotoscope-basins.webp",
  painted: "/rotoscope-painted.webp",
} as const;

const FLOOD_FRAMES = [
  "/rotoscope-flood-1.webp",
  "/rotoscope-flood-2.webp",
  "/rotoscope-flood-3.webp",
  "/rotoscope-flood-4.webp",
  STILLS.painted,
] as const;

function Still({ src, alt }: { src: string; alt: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      className={styles.still}
      src={src}
      alt={alt}
      width={STILL_WIDTH}
      height={STILL_HEIGHT}
      loading="lazy"
    />
  );
}

function Portrait({ alt }: { alt: string }) {
  return (
    <picture>
      <source srcSet="/rotoscope-portrait.avif" type="image/avif" />
      <source srcSet="/rotoscope-portrait.webp" type="image/webp" />
      <img
        className={styles.still}
        src="/rotoscope-portrait.jpg"
        alt={alt}
        width={STILL_WIDTH}
        height={STILL_HEIGHT}
        loading="lazy"
      />
    </picture>
  );
}

function Arrow() {
  return (
    <svg className={styles.arrow} viewBox="0 0 34 24" aria-hidden="true">
      <path d="M2 12H29M21 4L29 12L21 20" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const PIPELINE_STAGES = [
  {
    label: "Difference",
    src: STILLS.difference,
    alt: "Difference image of the portrait: edges and fine detail glow against black",
  },
  {
    label: "Features",
    src: STILLS.markers,
    alt: "Feature markers on the portrait, colored by face, body, and background tier",
  },
  {
    label: "Watershed",
    src: STILLS.basins,
    alt: "Catchment basin outlines traced over the portrait",
  },
  {
    label: "Average color",
    src: STILLS.painted,
    alt: "The portrait painted with one flat color per basin",
  },
] as const;

const STAGE_DWELL_MS = 3200;
const AUTOPLAY_IDLE_RESUME_MS = 10000;

const usePrefersReducedMotion = (): boolean => {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
};

/**
 * One stage at a time, matching the SynthesisPipeline stepper: autoplay loops
 * while the figure is in view, the dots double as navigation, and a manual
 * jump pauses autoplay until a short idle passes. Under prefers-reduced-motion
 * the four stages render side by side instead of auto-advancing.
 */
function PipelineOverview() {
  const { ref: inViewRef, inView } = useInView({
    threshold: 0.4,
    fallbackInView: true,
  });
  const reducedMotion = usePrefersReducedMotion();
  const [activeStage, setActiveStage] = useState(0);
  const [paused, setPaused] = useState(false);
  const resumeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const playing = inView && !paused && !reducedMotion;

  useEffect(() => {
    if (!playing) {
      return;
    }
    const timer = setInterval(() => {
      setActiveStage((stage) => (stage + 1) % PIPELINE_STAGES.length);
    }, STAGE_DWELL_MS);
    return () => clearInterval(timer);
  }, [playing]);

  useEffect(
    () => () => {
      if (resumeTimer.current) {
        clearTimeout(resumeTimer.current);
      }
    },
    [],
  );

  const jumpToStage = useCallback((index: number) => {
    setActiveStage(index);
    setPaused(true);
    if (resumeTimer.current) {
      clearTimeout(resumeTimer.current);
    }
    resumeTimer.current = setTimeout(() => {
      setPaused(false);
    }, AUTOPLAY_IDLE_RESUME_MS);
  }, []);

  if (reducedMotion) {
    return (
      <div className={styles.pipelineScroller} aria-label="The four rotoscoping stages">
        <div className={styles.pipeline}>
          {PIPELINE_STAGES.map((stage, index) => (
            <React.Fragment key={stage.label}>
              <figure className={styles.stage}>
                <figcaption>{stage.label}</figcaption>
                <Still src={stage.src} alt={stage.alt} />
              </figure>
              {index < PIPELINE_STAGES.length - 1 ? <Arrow /> : null}
            </React.Fragment>
          ))}
        </div>
      </div>
    );
  }

  return (
    <figure ref={inViewRef} className={styles.stepper} aria-label="The four rotoscoping stages">
      <figcaption className={styles.stepperCaption} aria-live="polite">
        {PIPELINE_STAGES[activeStage].label}
      </figcaption>
      <div className={styles.stepperStage}>
        {PIPELINE_STAGES.map((stage, index) => (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={stage.label}
            className={`${styles.still} ${styles.stepperFrame}`}
            data-active={index === activeStage}
            aria-hidden={index !== activeStage}
            src={stage.src}
            alt={stage.alt}
            width={STILL_WIDTH}
            height={STILL_HEIGHT}
            loading="lazy"
          />
        ))}
      </div>
      <ol className={styles.stepperDots} aria-label="Pipeline stages">
        {PIPELINE_STAGES.map((stage, index) => (
          <li key={stage.label} className={styles.stepperDotItem}>
            <button
              type="button"
              className={styles.stepperDot}
              data-active={index === activeStage}
              data-done={index < activeStage}
              aria-pressed={index === activeStage}
              aria-label={stage.label}
              onClick={() => jumpToStage(index)}
            />
          </li>
        ))}
      </ol>
    </figure>
  );
}

function DifferenceFigure() {
  return (
    <div className={styles.equation} aria-label="Portrait minus blurred copy equals difference">
      <figure className={styles.processPanel}>
        <figcaption>Portrait</figcaption>
        <Portrait alt="The source portrait" />
      </figure>
      <span className={styles.operator} aria-hidden="true">−</span>
      <figure className={styles.processPanel}>
        <figcaption>Blurred copy</figcaption>
        <Still src={STILLS.blurred} alt="Blurred grayscale copy of the portrait" />
      </figure>
      <span className={styles.operator} aria-hidden="true">=</span>
      <figure className={styles.processPanel}>
        <figcaption>Difference</figcaption>
        <Still
          src={STILLS.difference}
          alt="Difference between the portrait and its blurred copy, brightened for display"
        />
      </figure>
    </div>
  );
}

function MarkerFigure() {
  return (
    <figure className={styles.markerFigure}>
      <div className={styles.markerArt}>
        <Still
          src={STILLS.markers}
          alt="The 1,600 selected markers over a lightened portrait: blue on the face, orange on the body, gray in the background"
        />
      </div>
      <figcaption className={styles.markerLegend}>
        <span><i className={styles.faceDot} />Face 70%</span>
        <span><i className={styles.bodyDot} />Body 22%</span>
        <span><i className={styles.backgroundDot} />Background 8%</span>
      </figcaption>
    </figure>
  );
}

function WatershedFigure() {
  const [replayKey, setReplayKey] = useState(0);

  return (
    <figure className={styles.watershedFigure}>
      <div
        key={replayKey}
        className={styles.floodStack}
        role="img"
        aria-label="Markers flooding outward into catchment basins"
      >
        {FLOOD_FRAMES.map((src, index) => (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            key={src}
            className={index === 0 ? styles.floodBase : styles.floodFrame}
            style={{ "--delay": `${index * 600}ms` } as React.CSSProperties}
            src={src}
            alt=""
            width={STILL_WIDTH}
            height={STILL_HEIGHT}
            loading="lazy"
          />
        ))}
      </div>
      <figcaption>
        <button className={styles.replayButton} type="button" onClick={() => setReplayKey((key) => key + 1)}>
          Replay the flood
        </button>
      </figcaption>
    </figure>
  );
}

function PaintFigure() {
  return (
    <div className={styles.paintFigure}>
      <figure>
        <figcaption>Pixels</figcaption>
        <Portrait alt="The source portrait, hundreds of thousands of individual pixels" />
      </figure>
      <Arrow />
      <figure>
        <figcaption>Painted regions</figcaption>
        <Still
          src={STILLS.painted}
          alt="The finished rotoscope: every basin filled with its average color"
        />
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
          homepage portrait overrides them to 70/22/8 so the face keeps most of the
          budget, and that is the pass shown here.
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
          color. Hundreds of thousands of pixels become sixteen hundred painted
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
