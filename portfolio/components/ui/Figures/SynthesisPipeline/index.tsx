import React, { useCallback, useEffect, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { ShowcaseLabelFile } from "../AugmentationShowcase/labelGeometry";
import { GlyphSkeleton } from "./geometry";
import { ActView } from "./Acts";
import {
  ACT_COUNT,
  ACT_DWELL_MS,
  ACTS,
  AUTOPLAY_IDLE_RESUME_MS,
  composeStepsSrc,
  ComposeSteps,
  DEFAULT_MERCHANT,
  dotParamsSrc,
  DotParams,
  EMPTY_ASSETS,
  finalLabelsSrc,
  MERCHANT_LABELS,
  MERCHANTS,
  Merchant,
  MerchantAssets,
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
 * The act sequence AUTO-PLAYS in place when the figure scrolls into view (no
 * scroll-through track). The act dots double as navigation; any manual
 * interaction (dot, merchant toggle, weight slider) pauses autoplay, which
 * resumes after a short idle. A merchant toggle (Sprouts <-> Costco) swaps
 * every act's asset root. Under prefers-reduced-motion the acts render as a
 * static, fully-resolved stack.
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

  const [merchant, setMerchant] = useState<Merchant>(DEFAULT_MERCHANT);
  const [dotWeight, setDotWeight] = useState(1.0);
  const [assetsByMerchant, setAssetsByMerchant] = useState<
    Record<Merchant, MerchantAssets>
  >({ sprouts: EMPTY_ASSETS, costco: EMPTY_ASSETS });

  const [activeAct, setActiveAct] = useState(0);
  const [actProgress, setActProgress] = useState(0);
  const [paused, setPaused] = useState(false);

  const loadedRef = useRef<Set<Merchant>>(new Set());
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

  // Load the JSON assets for a merchant once, when the figure is near view.
  useEffect(() => {
    if (!inView || loadedRef.current.has(merchant)) {
      return;
    }
    loadedRef.current.add(merchant);
    let cancelled = false;

    const fetchJson = <T,>(url: string): Promise<T | null> =>
      fetch(url)
        .then((res) => (res.ok ? (res.json() as Promise<T>) : null))
        .catch(() => null);

    Promise.all([
      fetchJson<GlyphSkeleton>(skeletonSrc(merchant)),
      fetchJson<DotParams>(dotParamsSrc(merchant)),
      fetchJson<StyleAnnotated>(styleAnnotatedSrc(merchant)),
      fetchJson<ComposeSteps>(composeStepsSrc(merchant)),
      fetchJson<ShowcaseLabelFile>(finalLabelsSrc(merchant)),
    ]).then(([skeleton, dotParams, style, compose, finalLabels]) => {
      if (cancelled) {
        return;
      }
      setAssetsByMerchant((prev) => ({
        ...prev,
        [merchant]: { skeleton, dotParams, style, compose, finalLabels },
      }));
    });

    return () => {
      cancelled = true;
    };
  }, [inView, merchant]);

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

  const handleMerchant = useCallback(
    (m: Merchant) => {
      setMerchant(m);
      pauseForInteraction();
    },
    [pauseForInteraction],
  );

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

  const assets = assetsByMerchant[merchant];
  const activeMeta = ACTS[activeAct] ?? ACTS[0];

  const toggle = (
    <div className={styles.toggle} role="group" aria-label="Merchant">
      {MERCHANTS.map((m) => (
        <button
          key={m}
          type="button"
          className={styles.toggleButton}
          aria-pressed={m === merchant}
          onClick={() => handleMerchant(m)}
          data-testid={`merchant-${m}`}
        >
          {MERCHANT_LABELS[m]}
        </button>
      ))}
    </div>
  );

  const actProps = (progress: number, active: boolean) => ({
    merchant,
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
          {toggle}
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
      <div className={styles.topBar}>
        <div className={styles.heading}>
          <span className={styles.eyebrow} data-testid="act-eyebrow">
            {activeMeta.eyebrow}
          </span>
          <h3 className={styles.headline} data-testid="act-headline">
            {activeMeta.headline}
          </h3>
        </div>
        {toggle}
      </div>

      <div className={`${styles.stage} ${styles.interactiveStage}`}>
        <div key={activeMeta.id} className={styles.actWrap}>
          <ActView actId={activeMeta.id} {...actProps(actProgress, true)} />
        </div>
      </div>

      <p className={styles.caption} data-testid="act-caption">
        {activeMeta.caption}
      </p>

      <ol className={styles.progress} aria-label="Pipeline acts">
        {ACTS.map((meta) => (
          <li key={meta.id} className={styles.progressItem}>
            <button
              type="button"
              className={styles.progressDot}
              data-active={meta.index === activeAct}
              data-done={meta.index < activeAct}
              aria-pressed={meta.index === activeAct}
              aria-label={meta.eyebrow}
              onClick={() => jumpToAct(meta.index)}
              data-testid={`act-dot-${meta.id}`}
            />
          </li>
        ))}
      </ol>
    </div>
  );
};

export default SynthesisPipeline;
