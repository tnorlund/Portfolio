import React, { useCallback, useEffect, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { ShowcaseLabelFile } from "../AugmentationShowcase/labelGeometry";
import { GlyphSkeleton } from "./geometry";
import { ActView } from "./Acts";
import {
  ACT_COUNT,
  ACTS,
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
 * SynthesisPipeline — one scroll-driven figure telling how a labeled receipt
 * that never existed gets made end to end: letterforms mined from real prints,
 * style measured from real receipts, content composed, printed, and labeled.
 *
 * A single sticky stage renders whichever act the scroll position selects; a
 * merchant toggle (Sprouts <-> Costco) swaps every act's asset root. Under
 * prefers-reduced-motion the acts render as a static, fully-resolved stack.
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

interface ScrollState {
  activeAct: number;
  actProgress: number;
}

/**
 * Derive the active act + intra-act progress from how far a sticky scroller
 * has been scrolled through. Pure so the mapping is unit-testable.
 */
export const scrollStateFor = (
  rect: { top: number; height: number },
  viewportHeight: number,
  actCount: number,
): ScrollState => {
  const distance = rect.height - viewportHeight;
  if (distance <= 0) {
    return { activeAct: 0, actProgress: 0 };
  }
  const scrolled = Math.min(Math.max(-rect.top, 0), distance);
  const global = scrolled / distance;
  const actFloat = global * actCount;
  const activeAct = Math.min(actCount - 1, Math.max(0, Math.floor(actFloat)));
  const actProgress = Math.min(1, Math.max(0, actFloat - activeAct));
  return { activeAct, actProgress };
};

const SynthesisPipeline: React.FC = () => {
  const { ref: inViewRef, inView } = useInView({
    rootMargin: "200px 0px",
    triggerOnce: true,
    fallbackInView: true,
  });
  const reducedMotion = usePrefersReducedMotion();

  const [merchant, setMerchant] = useState<Merchant>(DEFAULT_MERCHANT);
  const [dotWeight, setDotWeight] = useState(1.0);
  const [assetsByMerchant, setAssetsByMerchant] = useState<
    Record<Merchant, MerchantAssets>
  >({ sprouts: EMPTY_ASSETS, costco: EMPTY_ASSETS });
  const [scroll, setScroll] = useState<ScrollState>({
    activeAct: 0,
    actProgress: 0,
  });

  const scrollerRef = useRef<HTMLDivElement>(null);
  const loadedRef = useRef<Set<Merchant>>(new Set());

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

  // Track scroll -> active act + progress (rAF-throttled). Skipped entirely
  // under reduced motion, where every act renders resolved.
  useEffect(() => {
    if (reducedMotion) {
      return;
    }
    let frame = 0;
    const measure = () => {
      frame = 0;
      const el = scrollerRef.current;
      if (!el) {
        return;
      }
      const rect = el.getBoundingClientRect();
      setScroll(
        scrollStateFor(
          { top: rect.top, height: rect.height },
          window.innerHeight,
          ACT_COUNT,
        ),
      );
    };
    const onScroll = () => {
      if (frame === 0) {
        frame = window.requestAnimationFrame(measure);
      }
    };
    measure();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      if (frame) {
        window.cancelAnimationFrame(frame);
      }
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [reducedMotion]);

  const assets = assetsByMerchant[merchant];

  const handleWeight = useCallback((w: number) => setDotWeight(w), []);

  const activeMeta = ACTS[scroll.activeAct] ?? ACTS[0];

  const toggle = (
    <div className={styles.toggle} role="group" aria-label="Merchant">
      {MERCHANTS.map((m) => (
        <button
          key={m}
          type="button"
          className={styles.toggleButton}
          aria-pressed={m === merchant}
          onClick={() => setMerchant(m)}
          data-testid={`merchant-${m}`}
        >
          {MERCHANT_LABELS[m]}
        </button>
      ))}
    </div>
  );

  const actProps = (index: number, progress: number, active: boolean) => ({
    merchant,
    assets,
    progress,
    active,
    reducedMotion,
    dotWeight,
    onWeightChange: handleWeight,
  });

  // ---- Reduced motion / no-scroll: static resolved stack ----
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
                  <ActView actId={meta.id} {...actProps(meta.index, 1, true)} />
                </div>
              </div>
              <p className={styles.caption}>{meta.caption}</p>
            </section>
          ))}
        </div>
      </div>
    );
  }

  // ---- Scroll-driven sticky stage ----
  return (
    <div
      ref={inViewRef}
      id="synthesis-pipeline"
      data-testid="synthesis-pipeline"
      data-mode="scroll"
      className={styles.container}
    >
      <div
        ref={scrollerRef}
        className={styles.scroller}
        style={{ height: `${ACT_COUNT * 95}vh` }}
      >
        <div className={styles.sticky}>
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

          <div className={styles.stage}>
            <div key={activeMeta.id} className={styles.actWrap}>
              <ActView
                actId={activeMeta.id}
                {...actProps(
                  scroll.activeAct,
                  scroll.actProgress,
                  true,
                )}
              />
            </div>
          </div>

          <p className={styles.caption} data-testid="act-caption">
            {activeMeta.caption}
          </p>

          <ol className={styles.progress} aria-hidden="true">
            {ACTS.map((meta) => (
              <li
                key={meta.id}
                className={styles.progressDot}
                data-active={meta.index === scroll.activeAct}
                data-done={meta.index < scroll.activeAct}
              />
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
};

export default SynthesisPipeline;
