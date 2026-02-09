import React, { useEffect, useMemo, useState } from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  JourneyReceipt,
  JourneyResponse,
  PhaseDecision,
  WordJourney,
} from "../../../../types/api";
import styles from "./JourneyVisualization.module.css";

// Phase display order and colors
const PHASE_CONFIG: Record<string, { color: string; shortName: string }> = {
  metadata_evaluation: { color: "#8b5cf6", shortName: "Meta" },
  currency_evaluation: { color: "#10b981", shortName: "Currency" },
  financial_validation: { color: "#3b82f6", shortName: "Financial" },
  phase3_llm_review: { color: "#f59e0b", shortName: "LLM" },
};

const PHASE_ORDER = [
  "metadata_evaluation",
  "currency_evaluation",
  "financial_validation",
  "phase3_llm_review",
];

const DECISION_COLORS: Record<string, string> = {
  VALID: "#22c55e",
  INVALID: "#ef4444",
  NEEDS_REVIEW: "#eab308",
};

const DECISION_ICONS: Record<string, string> = {
  VALID: "\u2713",
  INVALID: "\u2717",
  NEEDS_REVIEW: "?",
};

function getDecisionBg(decision: string): string {
  return DECISION_COLORS[decision] || "#6b7280";
}

function getPhaseColor(phase: string): string {
  return PHASE_CONFIG[phase]?.color || "#6b7280";
}

function getPhaseName(phase: string): string {
  return PHASE_CONFIG[phase]?.shortName || phase;
}

// --- Sub-components ---

function SummaryStats({
  receipts,
}: {
  receipts: JourneyReceipt[];
}) {
  const stats = useMemo(() => {
    let totalWords = 0;
    let multiPhase = 0;
    let conflicts = 0;
    for (const r of receipts) {
      if (r.summary) {
        totalWords += r.summary.total_words_evaluated;
        multiPhase += r.summary.multi_phase_words;
        conflicts += r.summary.words_with_conflicts;
      } else {
        totalWords += r.journeys.length;
        multiPhase += r.journeys.filter((j) => j.phases.length > 1).length;
        conflicts += r.journeys.filter((j) => j.has_conflict).length;
      }
    }
    return { totalWords, multiPhase, conflicts };
  }, [receipts]);

  return (
    <div className={styles.summaryBar}>
      <div className={styles.statItem}>
        <span className={styles.statValue}>{receipts.length}</span>
        <span className={styles.statLabel}>Receipts</span>
      </div>
      <div className={styles.statItem}>
        <span className={styles.statValue}>{stats.totalWords}</span>
        <span className={styles.statLabel}>Words Evaluated</span>
      </div>
      <div className={styles.statItem}>
        <span className={styles.statValue}>{stats.multiPhase}</span>
        <span className={styles.statLabel}>Multi-Phase</span>
      </div>
      <div className={styles.statItem}>
        <span className={styles.statValue}>{stats.conflicts}</span>
        <span className={styles.statLabel}>Conflicts</span>
      </div>
    </div>
  );
}

function PhaseLegend() {
  return (
    <div className={styles.phaseLegend}>
      {PHASE_ORDER.map((phase) => (
        <div key={phase} className={styles.legendItem}>
          <span
            className={styles.legendDot}
            style={{ background: getPhaseColor(phase) }}
          />
          {getPhaseName(phase)}
        </div>
      ))}
      <div className={styles.legendItem}>
        <span
          className={styles.legendDot}
          style={{ background: DECISION_COLORS.VALID }}
        />
        Valid
      </div>
      <div className={styles.legendItem}>
        <span
          className={styles.legendDot}
          style={{ background: DECISION_COLORS.INVALID }}
        />
        Invalid
      </div>
      <div className={styles.legendItem}>
        <span
          className={styles.legendDot}
          style={{ background: DECISION_COLORS.NEEDS_REVIEW }}
        />
        Needs Review
      </div>
    </div>
  );
}

function PhaseTimelineRow({ phases }: { phases: PhaseDecision[] }) {
  return (
    <div className={styles.phaseTimeline}>
      {phases.map((p, i) => (
        <React.Fragment key={`${p.phase}-${i}`}>
          {i > 0 && (
            <div
              className={styles.phaseConnector}
              style={{ background: getPhaseColor(p.phase) }}
            />
          )}
          <div className={styles.phaseNode}>
            <div
              className={styles.phaseDot}
              style={{ background: getDecisionBg(p.decision) }}
              title={`${getPhaseName(p.phase)}: ${p.decision}${p.confidence ? ` (${p.confidence})` : ""}`}
            >
              {DECISION_ICONS[p.decision] || "?"}
            </div>
            <span className={styles.phaseLabel}>{getPhaseName(p.phase)}</span>
          </div>
        </React.Fragment>
      ))}
    </div>
  );
}

function PhaseDetailPanel({ phase }: { phase: PhaseDecision }) {
  return (
    <div
      className={styles.phaseDetail}
      style={{ borderLeftColor: getPhaseColor(phase.phase) }}
    >
      <div className={styles.phaseDetailHeader}>
        <span className={styles.phaseDetailName}>
          {getPhaseName(phase.phase)}
        </span>
        <span
          className={styles.phaseDetailDecision}
          style={{ background: getDecisionBg(phase.decision) }}
        >
          {phase.decision}
        </span>
        {phase.confidence && (
          <span className={styles.phaseDetailConfidence}>
            {phase.confidence}
          </span>
        )}
      </div>
      <div>
        {phase.reasoning && (
          <div className={styles.phaseDetailReasoning}>{phase.reasoning}</div>
        )}
        {phase.suggested_label && (
          <div className={styles.phaseDetailSuggested}>
            Suggested: {phase.suggested_label}
          </div>
        )}
      </div>
    </div>
  );
}

function WordRow({ journey }: { journey: WordJourney }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className={`${styles.wordRow} ${journey.has_conflict ? styles.conflict : ""}`}
    >
      <div
        className={styles.wordRowHeader}
        onClick={() => setExpanded(!expanded)}
      >
        <span
          className={`${styles.expandIcon} ${expanded ? styles.expanded : ""}`}
        >
          {"\u25B6"}
        </span>
        <span className={styles.wordText} title={journey.word_text}>
          {journey.word_text}
        </span>
        <span className={styles.labelBadge}>{journey.current_label}</span>
        {journey.has_conflict && (
          <span className={styles.conflictIcon} title="Phases disagreed">
            {"\u26A0"}
          </span>
        )}
        <PhaseTimelineRow phases={journey.phases} />
        <span
          className={styles.outcomeBadge}
          style={{ background: getDecisionBg(journey.final_outcome) }}
        >
          {journey.final_outcome}
        </span>
      </div>
      {expanded && (
        <div className={styles.wordDetail}>
          {journey.phases.map((p, i) => (
            <PhaseDetailPanel key={`${p.phase}-${i}`} phase={p} />
          ))}
        </div>
      )}
    </div>
  );
}

// --- Main Component ---

export default function JourneyVisualization() {
  const [data, setData] = useState<JourneyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [conflictsOnly, setConflictsOnly] = useState(false);
  const [phaseFilter, setPhaseFilter] = useState<string>("all");
  const [labelFilter, setLabelFilter] = useState<string>("all");

  const { ref, inView } = useInView({ triggerOnce: true, threshold: 0.1 });

  useEffect(() => {
    if (!inView) return;
    let cancelled = false;
    setLoading(true);
    api
      .fetchLabelEvaluatorJourney(20)
      .then((resp) => {
        if (!cancelled) {
          setData(resp);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [inView]);

  const selectedReceipt = data?.receipts[selectedIndex] ?? null;

  // Collect all labels across all journeys for the filter dropdown
  const allLabels = useMemo(() => {
    if (!selectedReceipt) return [];
    const labels = new Set<string>();
    for (const j of selectedReceipt.journeys) {
      if (j.current_label) labels.add(j.current_label);
    }
    return Array.from(labels).sort();
  }, [selectedReceipt]);

  // Filtered journeys
  const filteredJourneys = useMemo(() => {
    if (!selectedReceipt) return [];
    let journeys = selectedReceipt.journeys;
    if (conflictsOnly) {
      journeys = journeys.filter((j) => j.has_conflict);
    }
    if (phaseFilter !== "all") {
      journeys = journeys.filter((j) =>
        j.phases.some((p) => p.phase === phaseFilter)
      );
    }
    if (labelFilter !== "all") {
      journeys = journeys.filter((j) => j.current_label === labelFilter);
    }
    return journeys;
  }, [selectedReceipt, conflictsOnly, phaseFilter, labelFilter]);

  return (
    <div ref={ref} className={styles.container}>
      {loading && <div className={styles.loading}>Loading journey data...</div>}
      {error && <div className={styles.error}>Error: {error}</div>}
      {data && (
        <>
          <SummaryStats receipts={data.receipts} />
          <PhaseLegend />

          {/* Receipt selector */}
          <div className={styles.receiptSelector}>
            {data.receipts.map((r, i) => {
              const conflictCount = r.summary
                ? r.summary.words_with_conflicts
                : r.journeys.filter((j) => j.has_conflict).length;
              const journeyCount = r.summary
                ? r.summary.total_words_evaluated
                : r.journeys.length;
              return (
                <div
                  key={`${r.image_id}-${r.receipt_id}`}
                  className={`${styles.receiptCard} ${i === selectedIndex ? styles.active : ""}`}
                  onClick={() => {
                    setSelectedIndex(i);
                    setConflictsOnly(false);
                    setPhaseFilter("all");
                    setLabelFilter("all");
                  }}
                >
                  <div className={styles.receiptCardMerchant}>
                    {r.merchant_name}
                  </div>
                  <div className={styles.receiptCardStats}>
                    <span>{journeyCount} words</span>
                    {conflictCount > 0 && (
                      <span className={styles.conflictBadge}>
                        {conflictCount} conflicts
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Filters */}
          {selectedReceipt && selectedReceipt.journeys.length > 0 && (
            <div className={styles.filters}>
              <button
                className={`${styles.filterToggle} ${conflictsOnly ? styles.active : ""}`}
                onClick={() => setConflictsOnly(!conflictsOnly)}
              >
                Conflicts only
              </button>
              <select
                className={styles.filterSelect}
                value={phaseFilter}
                onChange={(e) => setPhaseFilter(e.target.value)}
              >
                <option value="all">All phases</option>
                {PHASE_ORDER.map((p) => (
                  <option key={p} value={p}>
                    {getPhaseName(p)}
                  </option>
                ))}
              </select>
              <select
                className={styles.filterSelect}
                value={labelFilter}
                onChange={(e) => setLabelFilter(e.target.value)}
              >
                <option value="all">All labels</option>
                {allLabels.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Journey list */}
          <div className={styles.journeyList}>
            {filteredJourneys.length === 0 && selectedReceipt && (
              <div className={styles.emptyState}>
                {selectedReceipt.journeys.length === 0
                  ? "No words were evaluated for this receipt."
                  : "No words match the current filters."}
              </div>
            )}
            {filteredJourneys.map((j) => (
              <WordRow
                key={`${j.line_id}-${j.word_id}`}
                journey={j}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
