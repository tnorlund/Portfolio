import React, { useCallback, useEffect, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { ShowcaseLabelFile } from "../AugmentationShowcase/labelGeometry";
import { GlyphSkeleton } from "./geometry";
import { ActView } from "./Acts";
import { useActTransition } from "./actTransition";
import {
  ACT_COUNT,
  ACT_DWELL_MS,
  ACTS,
  AUTOPLAY_IDLE_RESUME_MS,
  composeStepsSrc,
  ComposeSteps,
  dotParamsSrc,
  DotParams,
  EMPTY_ASSETS,
  finalLabelsSrc,
  fontMetricsSrc,
  FontMetrics,
  MerchantAssets,
  PIPELINE_MERCHANT,
  skeletonSrc,
  styleAnnotatedSrc,
  StyleAnnotated,
} from "./pipelineData";
import styles from "./SynthesisPipeline.module.css";

/**
 * SynthesisPipeline — one figure telling how a labeled receipt that never
 * existed gets made end to end: letterforms mined from real prints, style
 * measured from real receipts, content composed, printed, and labeled.
 *
 * Acts 1-8 run a single merchant (Sprouts) end to end; a closing finale act
 * fans out to every merchant's printed receipt to make the generalization
 * point. The sequence AUTO-PLAYS in place when the figure scrolls into view
 * (no scroll-through track). The act dots double as navigation; any manual
 * interaction (dot, weight slider) pauses autoplay, which resumes after a
 * short idle. Under prefers-reduced-motion the acts render as a static,
 * fully-resolved stack.
 */

const usePrefersReducedMotion = (): boolean => {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
};

export interface AutoplayState {
  activeAct: number;
  actProgress: number;
}

/**
 * Advance the autoplay clock by `dtMs`. `dwellMs` is the current act's dwell;
 * progress rolls over into the next act (wrapping) when it passes 1. Pure so
 * the timeline math is unit-testable without a running rAF loop.
 */
export const advanceAutoplay = (
  activeAct: number,
  actProgress: number,
  dtMs: number,
  dwellMs: number,
  actCount: number,
): AutoplayState => {
  if (dwellMs <= 0 || actCount <= 0) {
    return { activeAct, actProgress: 1 };
  }
  let progress = actProgress + dtMs / dwellMs;
  let act = activeAct;
  while (progress >= 1) {
    progress -= 1;
    act = (act + 1) % actCount;
  }
  if (progress < 0) {
    progress = 0;
  }
  return { activeAct: act, actProgress: progress };
};

const SynthesisPipeline: React.FC = () => {
  const { ref: inViewRef, inView } = useInView({
    threshold: 0.4,
    fallbackInView: true,
  });
  const reducedMotion = usePrefersReducedMotion();

  const [dotWeight, setDotWeight] = useState(1.0);
  const [assets, setAssets] = useState<MerchantAssets>(EMPTY_ASSETS);

  const [activeAct, setActiveAct] = useState(0);
  const [actProgress, setActProgress] = useState(0);
  const [paused, setPaused] = useState(false);

  const loadedRef = useRef(false);
  const actRef = useRef(0);
  const progRef = useRef(0);
  const resumeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Mirror render state into refs so the rAF loop can resume from wherever the
  // timeline currently is (after a pause, a jump, or scrolling back in).
  useEffect(() => {
    actRef.current = activeAct;
  }, [activeAct]);
  useEffect(() => {
    progRef.current = actProgress;
  }, [actProgress]);

  // Load the Sprouts JSON assets once, when the figure is near view.
  useEffect(() => {
    if (!inView || loadedRef.current) {
      return;
    }
    loadedRef.current = true;
    let cancelled = false;

    const fetchJson = <T,>(url: string): Promise<T | null> =>
      fetch(url)
        .then((res) => (res.ok ? (res.json() as Promise<T>) : null))
        .catch(() => null);

    Promise.all([
      fetchJson<GlyphSkeleton>(skeletonSrc(PIPELINE_MERCHANT)),
      fetchJson<DotParams>(dotParamsSrc(PIPELINE_MERCHANT)),
      fetchJson<FontMetrics>(fontMetricsSrc(PIPELINE_MERCHANT)),
      fetchJson<StyleAnnotated>(styleAnnotatedSrc(PIPELINE_MERCHANT)),
      fetchJson<ComposeSteps>(composeStepsSrc(PIPELINE_MERCHANT)),
      fetchJson<ShowcaseLabelFile>(finalLabelsSrc(PIPELINE_MERCHANT)),
    ]).then(([skeleton, dotParams, fontMetrics, style, compose, finalLabels]) => {
      if (cancelled) {
        return;
      }
      setAssets({
        skeleton,
        dotParams,
        fontMetrics,
        style,
        compose,
        finalLabels,
      });
    });

    return () => {
      cancelled = true;
    };
  }, [inView]);

  const playing = inView && !paused && !reducedMotion;

  // Autoplay clock: while playing, advance the active act's progress each
  // frame, wrapping to the next act. Cancels (and preserves position via refs)
  // when playing stops — out of view, paused, or reduced motion.
  useEffect(() => {
    if (!playing) {
      return;
    }
    let raf = 0;
    let last = performance.now();
    let act = actRef.current;
    let prog = progRef.current;
    const tick = (ts: number) => {
      const dt = ts - last;
      last = ts;
      const dwell = ACT_DWELL_MS[ACTS[act].id];
      const next = advanceAutoplay(act, prog, dt, dwell, ACT_COUNT);
      act = next.activeAct;
      prog = next.actProgress;
      actRef.current = act;
      progRef.current = prog;
      setActiveAct(act);
      setActProgress(prog);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  useEffect(
    () => () => {
      if (resumeTimer.current) {
        clearTimeout(resumeTimer.current);
      }
    },
    [],
  );

  // Any manual interaction pauses autoplay, then schedules a resume.
  const pauseForInteraction = useCallback(() => {
    setPaused(true);
    if (resumeTimer.current) {
      clearTimeout(resumeTimer.current);
    }
    resumeTimer.current = setTimeout(() => {
      setPaused(false);
    }, AUTOPLAY_IDLE_RESUME_MS);
  }, []);

  const handleWeight = useCallback(
    (w: number) => {
      setDotWeight(w);
      pauseForInteraction();
    },
    [pauseForInteraction],
  );

  const jumpToAct = useCallback(
    (index: number) => {
      setActiveAct(index);
      setActProgress(1); // show the act resolved while paused
      actRef.current = index;
      progRef.current = 1;
      pauseForInteraction();
    },
    [pauseForInteraction],
  );

  // Crossfade machine: keeps the outgoing act mounted alongside the incoming
  // one for a short window so the stage dissolves between acts instead of
  // hard-cutting. Instant swap under reduced motion (animate = false).
  const transition = useActTransition(activeAct, !reducedMotion);

  const activeMeta = ACTS[activeAct] ?? ACTS[0];

  const actProps = (progress: number, active: boolean) => ({
    merchant: PIPELINE_MERCHANT,
    assets,
    progress,
    active,
    reducedMotion,
    dotWeight,
    onWeightChange: handleWeight,
  });

  // ---- Reduced motion: static resolved stack ----
  if (reducedMotion) {
    return (
      <div
        ref={inViewRef}
        id="synthesis-pipeline"
        data-testid="synthesis-pipeline"
        data-mode="static"
        className={styles.container}
      >
        <div className={styles.topBar}>
          <div className={styles.heading}>
            <span className={styles.eyebrow}>From receipts to receipts</span>
            <h3 className={styles.headline}>
              Synthesizing a labeled receipt, end to end
            </h3>
          </div>
        </div>
        <div className={styles.staticStack}>
          {ACTS.map((meta) => (
            <section
              key={meta.id}
              className={styles.staticAct}
              data-testid={`static-act-${meta.id}`}
            >
              <div className={styles.heading}>
                <span className={styles.eyebrow}>{meta.eyebrow}</span>
                <h4 className={styles.headline}>{meta.headline}</h4>
              </div>
              <div className={`${styles.stage} ${styles.staticStage}`}>
                <div className={styles.actWrap}>
                  <ActView actId={meta.id} {...actProps(1, true)} />
                </div>
              </div>
              <p className={styles.caption}>{meta.caption}</p>
            </section>
          ))}
        </div>
      </div>
    );
  }

  // Stage layers: the incoming act (progress-driven, active) plus, during a
  // crossfade, the outgoing act frozen at its resolved state. They stack and
  // overlap so one fades out while the other fades in.
  const stageLayers: Array<{
    id: string;
    index: number;
    phase: "entering" | "active" | "leaving";
    progress: number;
    active: boolean;
  }> = [];
  if (transition.leaving !== null) {
    const leavingMeta = ACTS[transition.leaving] ?? ACTS[0];
    stageLayers.push({
      id: leavingMeta.id,
      index: transition.leaving,
      phase: "leaving",
      progress: 1,
      active: false,
    });
  }
  const currentMeta = ACTS[transition.current] ?? ACTS[0];
  stageLayers.push({
    id: currentMeta.id,
    index: transition.current,
    phase: transition.leaving !== null ? "entering" : "active",
    progress: actProgress,
    active: true,
  });

  // ---- In-place autoplay ----
  return (
    <div
      ref={inViewRef}
      id="synthesis-pipeline"
      data-testid="synthesis-pipeline"
      data-mode="autoplay"
      data-paused={paused || undefined}
      className={styles.container}
    >
      {/* The content is the figure: no card, no titles, no captions. The act
          label survives only for screen readers and the e2e gate, announced
          politely as autoplay advances. */}
      <p
        className={styles.srOnly}
        aria-live="polite"
        data-testid="act-headline"
      >
        {activeMeta.headline}
      </p>

      <div
        className={`${styles.stage} ${styles.interactiveStage}`}
        data-testid="pipeline-stage"
      >
        {stageLayers.map((layer) => (
          <div
            key={layer.id}
            className={`${styles.actLayer} ${styles[layer.phase]}`}
            data-phase={layer.phase}
            data-testid={`act-layer-${layer.id}`}
            aria-hidden={layer.phase === "leaving"}
          >
            <ActView actId={layer.id as typeof activeMeta.id} {...actProps(layer.progress, layer.active)} />
          </div>
        ))}
      </div>

      <ol className={styles.progress} aria-label="Pipeline acts">
        {ACTS.map((meta) => {
          const isActive = meta.index === activeAct;
          return (
            <li key={meta.id} className={styles.progressItem}>
              <button
                type="button"
                className={styles.progressDot}
                data-active={isActive}
                data-done={meta.index < activeAct}
                aria-pressed={isActive}
                aria-label={meta.eyebrow}
                onClick={() => jumpToAct(meta.index)}
                data-testid={`act-dot-${meta.index}`}
              />
            </li>
          );
        })}
      </ol>
    </div>
  );
};

export default SynthesisPipeline;
