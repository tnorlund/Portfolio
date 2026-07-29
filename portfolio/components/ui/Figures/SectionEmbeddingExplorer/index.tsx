import React, {
  KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useInView } from "react-intersection-observer";
import { useActTransition } from "../SynthesisPipeline/actTransition";
import styles from "./SectionEmbeddingExplorer.module.css";
import {
  CHANGED_ROW_IDS,
  EXPLORER_ACTS,
  EXPERIMENT_METRICS,
  ExplorerActId,
  RECEIPT_ROWS,
  REFERENCE_RECEIPTS,
  ReferenceReceipt,
  SECTION_BY_ID,
  SectionId,
} from "./sectionData";

const AUTOPLAY_IDLE_RESUME_MS = 10_000;

const usePrefersReducedMotion = (): boolean => {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(media.matches);
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    media.addEventListener?.("change", onChange);
    return () => media.removeEventListener?.("change", onChange);
  }, []);

  return reduced;
};

export interface ExplorerAutoplayState {
  activeAct: number;
  actProgress: number;
}

export const advanceExplorerAutoplay = (
  activeAct: number,
  actProgress: number,
  dtMs: number,
  dwellMs: number,
  actCount: number,
): ExplorerAutoplayState => {
  if (dwellMs <= 0 || actCount <= 0) {
    return { activeAct, actProgress: 1 };
  }

  let progress = Math.max(0, actProgress + dtMs / dwellMs);
  let act = activeAct;
  while (progress >= 1) {
    progress -= 1;
    act = (act + 1) % actCount;
  }
  return { activeAct: act, actProgress: progress };
};

const phase = (value: number, start: number, end: number): number => {
  if (value <= start) return 0;
  if (value >= end) return 1;
  const t = (value - start) / (end - start);
  return t * t * (3 - 2 * t);
};

type Assignment = SectionId | null;

interface SectionSpan {
  section: SectionId;
  start: number;
  length: number;
}

const sectionSpans = (assignments: Assignment[]): SectionSpan[] => {
  const spans: SectionSpan[] = [];
  assignments.forEach((section, index) => {
    if (!section) return;
    const previous = spans[spans.length - 1];
    if (previous?.section === section && previous.start + previous.length === index) {
      previous.length += 1;
      return;
    }
    spans.push({ section, start: index, length: 1 });
  });
  return spans;
};

const assignmentsForAct = (
  actId: ExplorerActId,
  progress: number,
): Assignment[] => {
  if (actId === "ocr") return RECEIPT_ROWS.map(() => null);
  if (actId === "baseline" || actId === "neighbors") {
    return RECEIPT_ROWS.map((row) => row.baseline);
  }
  if (actId === "final") return RECEIPT_ROWS.map((row) => row.truth);

  return RECEIPT_ROWS.map((row) => {
    if (row.id === "subtotal" && progress >= 0.38) return row.truth;
    if (row.id === "visa" && progress >= 0.67) return row.truth;
    return row.baseline;
  });
};

const ACT_COPY: Record<
  ExplorerActId,
  { title: string; detail: string; evidence: string; sceneLabel: string }
> = {
  ocr: {
    title: "Start with complete Apple OCR rows.",
    detail: "LayoutLM labels words separately. This pass assigns one section to each complete row.",
    evidence: "LayoutLM → words · section decoder → rows",
    sceneLabel: "Apple OCR rows are visible on the current receipt without section assignments.",
  },
  baseline: {
    title: "Let the fair baseline draw its boundaries.",
    detail: "Two row-by-row guesses push ITEMS and SUMMARY one row too far.",
    evidence: "merchant identity · helpful, optional",
    sceneLabel: "Baseline section bands contain incorrect boundaries at subtotal and Visa rows.",
  },
  neighbors: {
    title: "Search labeled rows from other receipts.",
    detail: "OpenAI creates row embeddings; Chroma finds the nearest labeled rows.",
    evidence: "4 of 15 neighbors shown · 2-D map is schematic, not literal",
    sceneLabel: "Similar subtotal and payment rows are highlighted across the labeled receipt stack.",
  },
  corrected: {
    title: "Move the assignments downstream.",
    detail: "Neighbor votes and sequence order correct rows, then redraw contiguous boundaries.",
    evidence: "experimental geometry ×0.0 · receipt math ×0.0",
    sceneLabel: "Subtotal moves from items to summary and Visa moves from summary to payment.",
  },
  final: {
    title: "Finish with one clean section sequence.",
    detail: "The outlined rows changed; every section is contiguous on the front receipt.",
    evidence: `held-out row agreement +${EXPERIMENT_METRICS.deltaPoints} pp · ${EXPERIMENT_METRICS.fixed} fixed`,
    sceneLabel: "The current receipt has clean contiguous bands and highlights the two changed rows.",
  },
};

interface SectionBandsProps {
  assignments: Assignment[];
  opacity: number;
  compact?: boolean;
}

const SectionBands: React.FC<SectionBandsProps> = ({
  assignments,
  opacity,
  compact = false,
}) => (
  <div className={styles.sectionBands} aria-hidden="true">
    {sectionSpans(assignments).map((span, index) => (
      <div
        key={`${span.section}-${span.start}-${index}`}
        className={styles.sectionBand}
        data-section={span.section}
        data-compact={compact || undefined}
        style={
          {
            "--band-start": span.start,
            "--band-length": span.length,
            opacity,
          } as React.CSSProperties
        }
      >
        <span>{SECTION_BY_ID[span.section].shortLabel}</span>
      </div>
    ))}
  </div>
);

interface CurrentReceiptProps {
  actId: ExplorerActId;
  progress: number;
}

const CurrentReceipt: React.FC<CurrentReceiptProps> = ({ actId, progress }) => {
  const assignments = assignmentsForAct(actId, progress);
  const bandsVisible = actId === "ocr" ? phase(progress, 1.1, 1.2) : phase(progress, 0.08, 0.42);
  const rowReveal = actId === "ocr" ? progress : 1;
  const showNeighbors = actId === "neighbors";
  const showChanges = actId === "corrected" || actId === "final";

  return (
    <div
      className={`${styles.receiptCard} ${styles.currentReceipt}`}
      data-testid="section-current-receipt"
      data-assignment={actId}
      aria-hidden="true"
    >
      <div className={styles.receiptHeader}>
        <strong>SPROUTS</strong>
        <span>{actId === "ocr" ? "APPLE OCR" : "CURRENT"}</span>
      </div>
      <div className={styles.receiptRows}>
        <SectionBands assignments={assignments} opacity={bandsVisible} />
        {RECEIPT_ROWS.map((row, index) => {
          const reveal = phase(rowReveal, index * 0.035, 0.3 + index * 0.04);
          const assignment = assignments[index];
          const corrected = assignment === row.truth && row.baseline !== row.truth;
          return (
            <div
              key={row.id}
              className={styles.receiptRow}
              data-row-id={row.id}
              data-section={assignment ?? undefined}
              data-neighbor={showNeighbors && CHANGED_ROW_IDS.has(row.id) || undefined}
              data-changed={showChanges && corrected || undefined}
              data-testid={`section-row-${row.id}`}
              style={{
                opacity: reveal,
                transform: `translateY(${(1 - reveal) * 5}px)`,
              }}
            >
              <span>{row.text}</span>
              {row.amount ? <span>{row.amount}</span> : null}
            </div>
          );
        })}
      </div>
      <div className={styles.receiptFooter}>
        <span>11 complete rows</span>
        <span>{actId === "ocr" ? "unassigned" : "ordered"}</span>
      </div>
    </div>
  );
};

const ReferenceReceiptCard: React.FC<{
  receipt: ReferenceReceipt;
  index: number;
  showNeighbors: boolean;
  progress: number;
}> = ({ receipt, index, showNeighbors, progress }) => {
  const assignments = receipt.rows.map((row) => row.section);
  return (
    <div
      className={`${styles.receiptCard} ${styles.referenceReceipt}`}
      data-reference-index={index}
      data-testid={`section-reference-receipt-${index}`}
      aria-hidden="true"
      style={{ "--reference-index": index } as React.CSSProperties}
    >
      <div className={styles.receiptHeader}>
        <strong>{receipt.merchant}</strong>
        <span>LABELED</span>
      </div>
      <div className={styles.receiptRows}>
        <SectionBands assignments={assignments} opacity={1} compact />
        {receipt.rows.map((row) => {
          const isNeighbor = showNeighbors && Boolean(row.matches);
          const reveal = phase(progress, 0.14 + index * 0.06, 0.62 + index * 0.06);
          return (
            <div
              key={row.id}
              className={styles.receiptRow}
              data-section={row.section}
              data-neighbor={isNeighbor || undefined}
              data-neighbor-kind={row.matches}
              style={{ opacity: showNeighbors ? 0.46 + reveal * 0.54 : 1 }}
            >
              <span>{row.text}</span>
              {row.amount ? <span>{row.amount}</span> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
};

const NeighborConnections: React.FC<{ progress: number }> = ({ progress }) => {
  const reveal = phase(progress, 0.14, 0.78);
  return (
    <svg
      className={styles.neighborConnections}
      viewBox="0 0 900 450"
      aria-hidden="true"
      style={{ opacity: reveal }}
    >
      <path data-section="SUMMARY" d="M441 236 C474 226 486 166 507 155" pathLength="1" />
      <path data-section="PAYMENT" d="M441 303 C510 321 560 226 624 210" pathLength="1" />
      <path data-section="SUMMARY" d="M441 236 C551 276 640 216 732 208" pathLength="1" />
      <path data-section="PAYMENT" d="M441 303 C610 350 715 290 804 272" pathLength="1" />
      <circle data-section="SUMMARY" cx="441" cy="236" r="4" />
      <circle data-section="PAYMENT" cx="441" cy="303" r="4" />
      <circle data-section="SUMMARY" cx="507" cy="155" r="4" />
      <circle data-section="PAYMENT" cx="624" cy="210" r="4" />
      <circle data-section="SUMMARY" cx="732" cy="208" r="4" />
      <circle data-section="PAYMENT" cx="804" cy="272" r="4" />
    </svg>
  );
};

const EmbeddingInset: React.FC<{ progress: number }> = ({ progress }) => {
  const reveal = phase(progress, 0.36, 0.9);
  return (
    <div className={styles.embeddingInset} style={{ opacity: reveal }} aria-hidden="true">
      <svg viewBox="0 0 180 78">
        <g data-section="TRANSACTION_INFO"><circle cx="25" cy="54" r="12" /><circle cx="20" cy="51" r="2" /><circle cx="30" cy="58" r="2" /></g>
        <g data-section="ITEMS"><circle cx="63" cy="31" r="15" /><circle cx="58" cy="28" r="2" /><circle cx="69" cy="35" r="2" /></g>
        <g data-section="SUMMARY"><circle cx="118" cy="48" r="14" /><circle cx="113" cy="44" r="2" /><circle cx="124" cy="51" r="2" /></g>
        <g data-section="PAYMENT"><circle cx="154" cy="21" r="13" /><circle cx="150" cy="18" r="2" /><circle cx="159" cy="24" r="2" /></g>
        <path d="M103 38L116 46M103 38L148 23" />
        <circle className={styles.insetQuery} cx="103" cy="38" r="4" />
      </svg>
      <span>schematic 2-D map · not literal</span>
    </div>
  );
};

const ReceiptStackScene: React.FC<{
  actId: ExplorerActId;
  progress: number;
  reducedMotion: boolean;
}> = ({ actId, progress, reducedMotion }) => {
  const p = reducedMotion ? 1 : progress;
  const copy = ACT_COPY[actId];
  const showNeighbors = actId === "neighbors";

  return (
    <div
      className={styles.scene}
      data-act={actId}
      data-testid={`section-act-${actId}`}
      role="img"
      aria-label={copy.sceneLabel}
      style={{ "--act-progress": p } as React.CSSProperties}
    >
      <div className={styles.actAnnotation}>
        <span className={styles.actNumber}>{String(EXPLORER_ACTS.findIndex((act) => act.id === actId) + 1).padStart(2, "0")}</span>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>

      <div className={styles.stackField} data-testid="section-receipt-stack">
        {REFERENCE_RECEIPTS.map((receipt, index) => (
          <ReferenceReceiptCard
            key={receipt.id}
            receipt={receipt}
            index={index}
            showNeighbors={showNeighbors}
            progress={p}
          />
        ))}
        <CurrentReceipt actId={actId} progress={p} />
        {showNeighbors ? <NeighborConnections progress={p} /> : null}
        {showNeighbors ? <EmbeddingInset progress={p} /> : null}
      </div>

      <div className={styles.evidenceLine} data-testid="section-evidence-line">
        <span className={styles.evidenceRule} aria-hidden="true" />
        <span>{copy.evidence}</span>
      </div>
    </div>
  );
};

const SectionEmbeddingExplorer: React.FC = () => {
  const { ref: inViewRef, inView } = useInView({
    threshold: 0.4,
    fallbackInView: true,
  });
  const reducedMotion = usePrefersReducedMotion();
  const [activeAct, setActiveAct] = useState(0);
  const [actProgress, setActProgress] = useState(0);
  const [paused, setPaused] = useState(false);
  const activeRef = useRef(0);
  const progressRef = useRef(0);
  const resumeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const progressButtons = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    activeRef.current = activeAct;
  }, [activeAct]);

  useEffect(() => {
    progressRef.current = actProgress;
  }, [actProgress]);

  const playing = inView && !paused && !reducedMotion;
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    let act = activeRef.current;
    let progress = progressRef.current;

    const tick = (time: number) => {
      const next = advanceExplorerAutoplay(
        act,
        progress,
        time - last,
        EXPLORER_ACTS[act].dwellMs,
        EXPLORER_ACTS.length,
      );
      last = time;
      act = next.activeAct;
      progress = next.actProgress;
      activeRef.current = act;
      progressRef.current = progress;
      setActiveAct(act);
      setActProgress(progress);
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  useEffect(
    () => () => {
      if (resumeTimer.current) clearTimeout(resumeTimer.current);
    },
    [],
  );

  const pauseForInteraction = useCallback(() => {
    setPaused(true);
    if (resumeTimer.current) clearTimeout(resumeTimer.current);
    resumeTimer.current = setTimeout(() => setPaused(false), AUTOPLAY_IDLE_RESUME_MS);
  }, []);

  const jumpToAct = useCallback(
    (index: number) => {
      setActiveAct(index);
      setActProgress(1);
      activeRef.current = index;
      progressRef.current = 1;
      pauseForInteraction();
    },
    [pauseForInteraction],
  );

  const onProgressKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
      let next = index;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        next = (index + 1) % EXPLORER_ACTS.length;
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        next = (index - 1 + EXPLORER_ACTS.length) % EXPLORER_ACTS.length;
      } else if (event.key === "Home") {
        next = 0;
      } else if (event.key === "End") {
        next = EXPLORER_ACTS.length - 1;
      } else {
        return;
      }

      event.preventDefault();
      jumpToAct(next);
      progressButtons.current[next]?.focus();
    },
    [jumpToAct],
  );

  const transition = useActTransition(activeAct, !reducedMotion);
  const activeMeta = EXPLORER_ACTS[activeAct] ?? EXPLORER_ACTS[0];

  if (reducedMotion) {
    return (
      <section
        ref={inViewRef}
        id="section-embedding-explorer"
        data-testid="section-embedding-explorer"
        data-mode="static"
        className={styles.container}
        aria-labelledby="section-explorer-title"
      >
        <h3 id="section-explorer-title" className={styles.srOnly}>
          How row embeddings correct receipt section boundaries
        </h3>
        <div className={styles.staticStack}>
          {EXPLORER_ACTS.map((act) => (
            <section key={act.id} className={styles.staticAct}>
              <div className={`${styles.stage} ${styles.staticStage}`}>
                <ReceiptStackScene actId={act.id} progress={1} reducedMotion />
              </div>
            </section>
          ))}
        </div>
      </section>
    );
  }

  const layers: Array<{
    id: ExplorerActId;
    phase: "entering" | "active" | "leaving";
    progress: number;
  }> = [];

  if (transition.leaving !== null) {
    layers.push({
      id: EXPLORER_ACTS[transition.leaving].id,
      phase: "leaving",
      progress: 1,
    });
  }

  layers.push({
    id: EXPLORER_ACTS[transition.current].id,
    phase: transition.leaving !== null ? "entering" : "active",
    progress: actProgress,
  });

  return (
    <section
      ref={inViewRef}
      id="section-embedding-explorer"
      data-testid="section-embedding-explorer"
      data-mode="autoplay"
      data-paused={paused || undefined}
      className={styles.container}
      aria-labelledby="section-explorer-title"
      aria-describedby="section-explorer-description"
    >
      <h3 id="section-explorer-title" className={styles.srOnly}>
        How row embeddings correct receipt section boundaries
      </h3>
      <p id="section-explorer-description" className={styles.srOnly}>
        A five-step explainer. Apple OCR provides complete rows. LayoutLM labels individual words, while this visualization assigns sections to complete rows. OpenAI creates row embeddings and Chroma searches labeled rows from other receipts. The two-dimensional map is schematic. Merchant identity is optional. Experimental geometry and receipt math currently have zero weight.
      </p>
      <p className={styles.srOnly} aria-live="polite" data-testid="section-act-label">
        {activeMeta.accessibleLabel}
      </p>

      <div className={`${styles.stage} ${styles.interactiveStage}`} data-testid="section-explorer-stage">
        {layers.map((layer) => (
          <div
            key={layer.id}
            className={`${styles.actLayer} ${styles[layer.phase]}`}
            data-phase={layer.phase}
            aria-hidden={layer.phase === "leaving"}
          >
            <ReceiptStackScene actId={layer.id} progress={layer.progress} reducedMotion={false} />
          </div>
        ))}
      </div>

      <ol className={styles.progress} aria-label="Section assignment steps">
        {EXPLORER_ACTS.map((act) => {
          const isActive = act.index === activeAct;
          return (
            <li key={act.id}>
              <button
                ref={(node) => { progressButtons.current[act.index] = node; }}
                type="button"
                className={styles.progressButton}
                data-active={isActive}
                data-done={act.index < activeAct}
                aria-pressed={isActive}
                aria-label={act.accessibleLabel}
                title={act.label}
                onClick={() => jumpToAct(act.index)}
                onKeyDown={(event) => onProgressKeyDown(event, act.index)}
                data-testid={`section-act-dot-${act.index}`}
              >
                <span aria-hidden="true" />
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
};

export default SectionEmbeddingExplorer;
