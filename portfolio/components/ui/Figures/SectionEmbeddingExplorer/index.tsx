import React, { useCallback, useEffect, useRef, useState } from "react";
import { useInView } from "react-intersection-observer";
import { useActTransition } from "../SynthesisPipeline/actTransition";
import styles from "./SectionEmbeddingExplorer.module.css";
import {
  EXPLORER_ACTS,
  EXPERIMENT_METRICS,
  ExplorerActId,
  nearestProjectionPoints,
  PROJECTION_POINTS,
  QUERY_BY_ID,
  QUERY_SCENARIOS,
  QueryScenario,
  RECEIPT_ROWS,
  SECTION_BY_ID,
  SECTIONS,
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

const rowY = (index: number): number => 52 + index * 24;
const sectionX = (section: SectionId): number =>
  190 + SECTIONS.findIndex((candidate) => candidate.id === section) * 110;

interface ScenarioControlProps {
  selected: QueryScenario["id"];
  onSelect: (id: QueryScenario["id"]) => void;
}

const ScenarioControl: React.FC<ScenarioControlProps> = ({
  selected,
  onSelect,
}) => (
  <div className={styles.queryControl} aria-label="Row to classify">
    {QUERY_SCENARIOS.map((scenario) => (
      <button
        key={scenario.id}
        type="button"
        className={styles.queryButton}
        data-active={selected === scenario.id}
        aria-pressed={selected === scenario.id}
        onClick={() => onSelect(scenario.id)}
      >
        {scenario.label}
      </button>
    ))}
  </div>
);

const SectionPill: React.FC<{ section: SectionId; muted?: boolean }> = ({
  section,
  muted = false,
}) => (
  <span
    className={styles.sectionPill}
    data-section={section}
    data-muted={muted || undefined}
  >
    <span className={styles.sectionDot} aria-hidden="true" />
    {SECTION_BY_ID[section].shortLabel}
  </span>
);

interface ActProps extends ScenarioControlProps {
  progress: number;
  reducedMotion: boolean;
}

const ReceiptAct: React.FC<ActProps> = ({ progress, reducedMotion }) => {
  const p = reducedMotion ? 1 : progress;
  return (
    <div className={styles.receiptAct} data-testid="section-act-receipt">
      <div className={styles.actAnnotation}>
        <span className={styles.actNumber}>01</span>
        <strong>Keep the receipt in reading order.</strong>
        <span>Each line is one row the decoder must place.</span>
      </div>
      <div className={styles.receiptPaper} aria-hidden="true">
        {RECEIPT_ROWS.map((row, index) => {
          const reveal = phase(p, index * 0.045, 0.32 + index * 0.045);
          return (
            <div
              key={row.id}
              className={styles.receiptRow}
              data-section={row.truth}
              style={{
                opacity: reveal,
                transform: `translateY(${(1 - reveal) * 8}px)`,
              }}
            >
              <span>{row.text}</span>
              {row.amount ? <span>{row.amount}</span> : null}
            </div>
          );
        })}
        <div className={styles.receiptSectionRail}>
          {SECTIONS.map((section) => {
            const rows = RECEIPT_ROWS.filter((row) => row.truth === section.id);
            const first = RECEIPT_ROWS.findIndex(
              (row) => row.truth === section.id,
            );
            return (
              <div
                key={section.id}
                className={styles.railSegment}
                data-section={section.id}
                style={{
                  top: `${first * 24 + 7}px`,
                  height: `${rows.length * 24 - 5}px`,
                  opacity: phase(p, 0.52, 0.85),
                }}
              >
                <span>{section.shortLabel}</span>
              </div>
            );
          })}
        </div>
      </div>
      <p className={styles.stageNote}>Schematic receipt · section order is real</p>
    </div>
  );
};

const ProjectionSvg: React.FC<{
  scenario: QueryScenario;
  progress: number;
  showNeighbors: boolean;
}> = ({ scenario, progress, showNeighbors }) => {
  const neighbors = nearestProjectionPoints(scenario);
  const settle = phase(progress, 0.05, 0.72);
  const lineReveal = phase(progress, 0.18, 0.82);
  const queryX = 72 + scenario.x * 5.55;
  const queryY = 326 - scenario.y * 2.8;
  return (
    <svg
      className={styles.projectionSvg}
      viewBox="0 0 680 350"
      role="img"
      aria-label={
        showNeighbors
          ? `Fifteen cross-receipt neighbors vote on ${scenario.label}`
          : `${scenario.label} projected among labeled receipt row clusters`
      }
    >
      <g className={styles.axes}>
        <path d="M72 24V326H642" />
        <text x="78" y="42">semantic row embeddings</text>
        <text x="638" y="342" textAnchor="end">schematic 2-D view</text>
      </g>

      {showNeighbors
        ? neighbors.map((neighbor, index) => {
            const x = 72 + neighbor.x * 5.55;
            const y = 326 - neighbor.y * 2.8;
            const reveal = phase(lineReveal, index / 22, 0.5 + index / 30);
            return (
              <line
                key={`line-${neighbor.id}`}
                className={styles.neighborLine}
                data-section={neighbor.section}
                x1={queryX}
                y1={queryY}
                x2={queryX + (x - queryX) * reveal}
                y2={queryY + (y - queryY) * reveal}
                opacity={0.12 + reveal * 0.34}
              />
            );
          })
        : null}

      {SECTIONS.map((section) => {
        const points = PROJECTION_POINTS.filter(
          (point) => point.section === section.id,
        );
        const cx = points.reduce((sum, point) => sum + point.x, 0) / points.length;
        const cy = points.reduce((sum, point) => sum + point.y, 0) / points.length;
        return (
          <g key={section.id} data-section={section.id}>
            <ellipse
              className={styles.clusterHalo}
              cx={72 + cx * 5.55}
              cy={326 - cy * 2.8}
              rx={58 * settle}
              ry={34 * settle}
              opacity={0.08 + settle * 0.09}
            />
            <text
              className={styles.clusterLabel}
              x={72 + cx * 5.55}
              y={326 - cy * 2.8 - 42}
              textAnchor="middle"
              opacity={settle}
            >
              {section.shortLabel}
            </text>
          </g>
        );
      })}

      {PROJECTION_POINTS.map((point, index) => {
        const destinationX = 72 + point.x * 5.55;
        const destinationY = 326 - point.y * 2.8;
        const localSettle = phase(
          progress,
          0.03 + (index % 8) * 0.018,
          0.5 + (index % 8) * 0.018,
        );
        const isNeighbor = showNeighbors && neighbors.some((n) => n.id === point.id);
        return (
          <circle
            key={point.id}
            className={styles.projectionPoint}
            data-section={point.section}
            data-neighbor={isNeighbor || undefined}
            cx={340 + (destinationX - 340) * localSettle}
            cy={175 + (destinationY - 175) * localSettle}
            r={isNeighbor ? 5 : 3.5}
            opacity={showNeighbors && !isNeighbor ? 0.18 : 0.76}
          >
            <title>{`${point.merchant} · ${SECTION_BY_ID[point.section].label}`}</title>
          </circle>
        );
      })}

      <g
        className={styles.queryPoint}
        transform={`translate(${340 + (queryX - 340) * settle} ${
          175 + (queryY - 175) * settle
        })`}
      >
        <circle r="10" />
        <circle className={styles.queryPulse} r="16" opacity={showNeighbors ? 0.7 : 0.35} />
        <text x="14" y="-14">{scenario.label}</text>
      </g>
    </svg>
  );
};

const ProjectionAct: React.FC<ActProps> = ({
  progress,
  reducedMotion,
  selected,
  onSelect,
}) => {
  const scenario = QUERY_BY_ID[selected];
  return (
    <div className={styles.projectionAct} data-testid="section-act-projection">
      <div className={styles.actAnnotation}>
        <span className={styles.actNumber}>02</span>
        <strong>Project wording and context into Chroma.</strong>
        <span>Nearby rows have been used in the same kind of section.</span>
      </div>
      <ProjectionSvg
        scenario={scenario}
        progress={reducedMotion ? 1 : progress}
        showNeighbors={false}
      />
      <ScenarioControl selected={selected} onSelect={onSelect} />
    </div>
  );
};

const VoteBars: React.FC<{ scenario: QueryScenario; progress: number }> = ({
  scenario,
  progress,
}) => (
  <div className={styles.votePanel} aria-label="Cosine-weighted neighbor votes">
    <div className={styles.voteHeader}>
      <span>15 neighbors</span>
      <strong>cosine-weighted vote</strong>
    </div>
    {SECTIONS.map((section, index) => {
      const value = scenario.votes[section.id];
      const reveal = phase(progress, 0.36 + index * 0.06, 0.78 + index * 0.04);
      return (
        <div key={section.id} className={styles.voteRow} data-section={section.id}>
          <span>{section.shortLabel}</span>
          <div className={styles.voteTrack}>
            <span style={{ width: `${value * reveal * 100}%` }} />
          </div>
          <strong>{Math.round(value * 100)}%</strong>
        </div>
      );
    })}
    <div className={styles.weightEquation}>
      <span>KNN × <strong>1.0</strong></span>
      <span>centroid × <strong>0.5</strong></span>
    </div>
  </div>
);

const NeighborsAct: React.FC<ActProps> = ({
  progress,
  reducedMotion,
  selected,
  onSelect,
}) => {
  const scenario = QUERY_BY_ID[selected];
  const p = reducedMotion ? 1 : progress;
  return (
    <div className={styles.neighborsAct} data-testid="section-act-neighbors">
      <div className={styles.actAnnotation}>
        <span className={styles.actNumber}>03</span>
        <strong>Ask labeled rows from other receipts.</strong>
        <span>Same-receipt rows are excluded; closer cosine matches vote harder.</span>
      </div>
      <div className={styles.neighborLayout}>
        <ProjectionSvg scenario={scenario} progress={p} showNeighbors />
        <VoteBars scenario={scenario} progress={p} />
      </div>
      <ScenarioControl selected={selected} onSelect={onSelect} />
    </div>
  );
};

const DecodeAct: React.FC<ActProps> = ({
  progress,
  reducedMotion,
  selected,
  onSelect,
}) => {
  const p = reducedMotion ? 1 : progress;
  const selectedScenario = QUERY_BY_ID[selected];
  const scan = phase(p, 0.05, 0.9);
  const correctedRows = new Set(["subtotal", "visa"]);
  const baselinePath = RECEIPT_ROWS.map((row, index) =>
    `${index === 0 ? "M" : "L"}${sectionX(row.baseline)} ${rowY(index)}`,
  ).join(" ");
  const decodedPath = RECEIPT_ROWS.map((row, index) =>
    `${index === 0 ? "M" : "L"}${sectionX(row.truth)} ${rowY(index)}`,
  ).join(" ");
  return (
    <div className={styles.decodeAct} data-testid="section-act-decode">
      <div className={styles.actAnnotation}>
        <span className={styles.actNumber}>04</span>
        <strong>Decode the whole receipt, not isolated rows.</strong>
        <span>
          Transition and duration priors prefer contiguous spans. Added
          geometry/math stayed at ×0.0.
        </span>
      </div>
      <div className={styles.decodeChart}>
        <svg
          viewBox="0 0 660 330"
          role="img"
          aria-label="Baseline row guesses compared with the contiguous semi-Markov decoded path"
        >
          {SECTIONS.map((section) => (
            <g key={section.id} data-section={section.id}>
              <text className={styles.decodeHeader} x={sectionX(section.id)} y="22" textAnchor="middle">
                {section.shortLabel}
              </text>
              <line className={styles.decodeColumn} x1={sectionX(section.id)} y1="34" x2={sectionX(section.id)} y2="306" />
            </g>
          ))}
          {RECEIPT_ROWS.map((row, index) => {
            const y = rowY(index);
            const revealed = scan >= (index + 1) / RECEIPT_ROWS.length;
            return (
              <g key={row.id} opacity={revealed ? 1 : 0.16}>
                <text className={styles.decodeRowLabel} x="8" y={y + 4}>
                  {row.id === "merchant" ? "merchant" : row.text.slice(0, 14)}
                </text>
                {correctedRows.has(row.id) ? (
                  <circle
                    className={styles.correctionHalo}
                    data-section={row.truth}
                    cx={sectionX(row.truth)}
                    cy={y}
                    r="11"
                  />
                ) : null}
              </g>
            );
          })}
          <path className={styles.baselinePath} d={baselinePath} pathLength="1" strokeDasharray="1" strokeDashoffset={1 - scan} />
          <path className={styles.decodedPath} d={decodedPath} pathLength="1" strokeDasharray="1" strokeDashoffset={1 - phase(p, 0.32, 1)} />
        </svg>
        <div className={styles.decodeComparison}>
          <span>row alone</span>
          <SectionPill section={selectedScenario.baseline} muted />
          <span className={styles.decodeArrow} aria-hidden="true">→</span>
          <span>full sequence</span>
          <SectionPill section={selectedScenario.decoded} />
        </div>
      </div>
      <ScenarioControl selected={selected} onSelect={onSelect} />
    </div>
  );
};

const ResultAct: React.FC<ActProps> = ({ progress, reducedMotion }) => {
  const p = reducedMotion ? 1 : progress;
  const baseline = phase(p, 0.08, 0.5);
  const hybrid = phase(p, 0.28, 0.82);
  return (
    <div className={styles.resultAct} data-testid="section-act-result">
      <div className={styles.actAnnotation}>
        <span className={styles.actNumber}>05</span>
        <strong>Then make the holdout decide.</strong>
        <span>167 unseen receipts · 4,214 rows · receipt-grouped split</span>
      </div>
      <div className={styles.resultBars}>
        <div className={styles.resultRow}>
          <span>fair baseline</span>
          <div className={styles.resultTrack}>
            <span className={styles.baselineFill} style={{ width: `${EXPERIMENT_METRICS.baselineAgreement * baseline}%` }} />
          </div>
          <strong>{EXPERIMENT_METRICS.baselineAgreement}%</strong>
        </div>
        <div className={styles.resultRow}>
          <span>Chroma hybrid</span>
          <div className={styles.resultTrack}>
            <span className={styles.hybridFill} style={{ width: `${EXPERIMENT_METRICS.hybridAgreement * hybrid}%` }} />
          </div>
          <strong>{EXPERIMENT_METRICS.hybridAgreement}%</strong>
        </div>
      </div>
      <div className={styles.deltaCallout} style={{ opacity: phase(p, 0.48, 0.78) }}>
        <strong>+{EXPERIMENT_METRICS.deltaPoints} pp</strong>
        <span>row agreement</span>
      </div>
      <div className={styles.pairedCounts} style={{ opacity: phase(p, 0.62, 0.94) }}>
        <div>
          <strong>{EXPERIMENT_METRICS.fixed}</strong>
          <span>rows fixed</span>
        </div>
        <span className={styles.pairedRule} aria-hidden="true" />
        <div>
          <strong>{EXPERIMENT_METRICS.regressed}</strong>
          <span>rows regressed</span>
        </div>
      </div>
      <p className={styles.resultFootnote}>
        95% receipt bootstrap: +{EXPERIMENT_METRICS.bootstrapLow} to +{EXPERIMENT_METRICS.bootstrapHigh} pp · embedding coverage {EXPERIMENT_METRICS.coverage}%
      </p>
    </div>
  );
};

const ActView: React.FC<ActProps & { actId: ExplorerActId }> = ({
  actId,
  ...props
}) => {
  switch (actId) {
    case "receipt":
      return <ReceiptAct {...props} />;
    case "projection":
      return <ProjectionAct {...props} />;
    case "neighbors":
      return <NeighborsAct {...props} />;
    case "decode":
      return <DecodeAct {...props} />;
    case "result":
      return <ResultAct {...props} />;
    default:
      return null;
  }
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
  const [selected, setSelected] = useState<QueryScenario["id"]>("subtotal");
  const activeRef = useRef(0);
  const progressRef = useRef(0);
  const resumeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    resumeTimer.current = setTimeout(
      () => setPaused(false),
      AUTOPLAY_IDLE_RESUME_MS,
    );
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

  const selectScenario = useCallback(
    (id: QueryScenario["id"]) => {
      setSelected(id);
      pauseForInteraction();
    },
    [pauseForInteraction],
  );

  const transition = useActTransition(activeAct, !reducedMotion);
  const activeMeta = EXPLORER_ACTS[activeAct] ?? EXPLORER_ACTS[0];
  const actProps = (progress: number): ActProps => ({
    progress,
    reducedMotion,
    selected,
    onSelect: selectScenario,
  });

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
          How cross-receipt embeddings improve receipt section classification
        </h3>
        <div className={styles.staticStack}>
          {EXPLORER_ACTS.map((act) => (
            <section key={act.id} className={styles.staticAct}>
              <div className={`${styles.stage} ${styles.staticStage}`}>
                <ActView actId={act.id} {...actProps(1)} />
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
        How cross-receipt embeddings improve receipt section classification
      </h3>
      <p id="section-explorer-description" className={styles.srOnly}>
        An animated five-step explainer. Receipt rows are projected into Chroma
        embedding space. Fifteen labeled rows from other receipts cast
        cosine-weighted section votes. A semi-Markov decoder combines those
        votes with the ordered receipt sequence. On 167 held-out receipts, row
        agreement rises from 85.95 to 90.84 percent. Geometry and arithmetic
        evidence was tested separately and received weight zero because it did
        not improve validation results.
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
            <ActView actId={layer.id} {...actProps(layer.progress)} />
          </div>
        ))}
      </div>

      <ol className={styles.progress} aria-label="Section classifier steps">
        {EXPLORER_ACTS.map((act) => {
          const isActive = act.index === activeAct;
          return (
            <li key={act.id}>
              <button
                type="button"
                className={styles.progressButton}
                data-active={isActive}
                data-done={act.index < activeAct}
                aria-pressed={isActive}
                aria-label={act.accessibleLabel}
                title={act.label}
                onClick={() => jumpToAct(act.index)}
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
