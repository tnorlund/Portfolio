import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useInView } from "react-intersection-observer";
import { api } from "../../../../services/api";
import {
  EpochEvaluationEntry,
  EpochEvaluationReceiptRecord,
  EpochEvaluationResponse,
} from "../../../../types/api";
import { getBestImageUrl } from "../../../../utils/imageFormat";
import { useImageFormatSupport } from "../ReceiptFlow/useImageFormatSupport";
import styles from "./EpochEvaluation.module.css";

// ---------------------------------------------------------------------------
// Label presentation (mirrors the other LayoutLM figures)
// ---------------------------------------------------------------------------

const normalizeLabel = (label: string): string =>
  label === "ADDRESS_LINE" ? "ADDRESS" : label;

const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  AMOUNT: "var(--color-green)",
  ADDRESS: "var(--color-red)",
  PHONE_NUMBER: "var(--color-pink)",
  WEBSITE: "var(--color-purple)",
  STORE_HOURS: "var(--color-orange)",
  PAYMENT_METHOD: "var(--color-orange)",
};

const labelColor = (label: string): string =>
  LABEL_COLORS[normalizeLabel(label)] || "var(--color-gray, #888)";

const formatLabel = (label: string): string => {
  const n = normalizeLabel(label);
  if (n === "O") return "None";
  return n
    .split("_")
    .map((w) => w.charAt(0) + w.slice(1).toLowerCase())
    .join(" ");
};

const isAggregate = (key: string): boolean => key.toLowerCase().includes("avg");

// ---------------------------------------------------------------------------
// Held-out vs training-reported F1 curve
// ---------------------------------------------------------------------------

const VB_W = 600;
const VB_H = 190;
const PAD_L = 34;
const PAD_R = 12;
const PAD_T = 16;
const PAD_B = 24;

interface CurveProps {
  epochs: EpochEvaluationEntry[];
  bestHeldoutEpoch: number | null;
  bestReportedEpoch: number | null;
  selectedEpoch: number | null;
  onSelect: (epoch: number) => void;
}

const EpochCurveChart: React.FC<CurveProps> = ({
  epochs,
  bestHeldoutEpoch,
  bestReportedEpoch,
  selectedEpoch,
  onSelect,
}) => {
  const svgRef = useRef<SVGSVGElement>(null);

  const minEpoch = epochs[0].epoch as number;
  const maxEpoch = epochs[epochs.length - 1].epoch as number;
  const epochSpan = Math.max(1, maxEpoch - minEpoch);

  // y-domain: pad the top a little above the best score, floor at 0.
  const allF1 = epochs.flatMap((e) =>
    [e.heldout_f1, e.training_reported_f1 ?? 0].filter((v) => v != null)
  );
  const yMax = Math.min(1, Math.max(0.1, Math.max(...allF1) * 1.12));

  const xOf = useCallback(
    (epoch: number) =>
      PAD_L + ((epoch - minEpoch) / epochSpan) * (VB_W - PAD_L - PAD_R),
    [minEpoch, epochSpan]
  );
  const yOf = useCallback(
    (f1: number) => PAD_T + (1 - f1 / yMax) * (VB_H - PAD_T - PAD_B),
    [yMax]
  );

  const heldoutPath = useMemo(
    () =>
      epochs
        .map(
          (e, i) =>
            `${i === 0 ? "M" : "L"}${xOf(e.epoch as number).toFixed(1)} ${yOf(
              e.heldout_f1
            ).toFixed(1)}`
        )
        .join(" "),
    [epochs, xOf, yOf]
  );

  const reportedPts = epochs.filter((e) => e.training_reported_f1 != null);
  const reportedPath = useMemo(
    () =>
      reportedPts
        .map(
          (e, i) =>
            `${i === 0 ? "M" : "L"}${xOf(e.epoch as number).toFixed(1)} ${yOf(
              e.training_reported_f1 as number
            ).toFixed(1)}`
        )
        .join(" "),
    [reportedPts, xOf, yOf]
  );

  const handlePointer = useCallback(
    (clientX: number) => {
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const vbX = ((clientX - rect.left) / rect.width) * VB_W;
      // nearest epoch by x
      let nearest = epochs[0];
      let best = Infinity;
      for (const e of epochs) {
        const d = Math.abs(xOf(e.epoch as number) - vbX);
        if (d < best) {
          best = d;
          nearest = e;
        }
      }
      onSelect(nearest.epoch as number);
    },
    [epochs, xOf, onSelect]
  );

  // y gridlines at 0, 0.25, 0.5, 0.75, 1 (only those within domain)
  const gridY = [0, 0.25, 0.5, 0.75, 1].filter((v) => v <= yMax + 0.001);

  const selectedEntry = epochs.find((e) => e.epoch === selectedEpoch);

  return (
    <svg
      ref={svgRef}
      className={styles.chart}
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      role="img"
      aria-label="Held-out F1 across epochs"
      onPointerMove={(e) => e.buttons !== 0 && handlePointer(e.clientX)}
      onPointerDown={(e) => handlePointer(e.clientX)}
    >
      {/* gridlines + y labels */}
      {gridY.map((v) => (
        <g key={v}>
          <line
            x1={PAD_L}
            x2={VB_W - PAD_R}
            y1={yOf(v)}
            y2={yOf(v)}
            stroke="var(--color-border, #eee)"
            strokeWidth={1}
          />
          <text
            x={PAD_L - 6}
            y={yOf(v) + 3}
            textAnchor="end"
            fontSize={9}
            fill="var(--color-text-muted, #aaa)"
          >
            {v.toFixed(2)}
          </text>
        </g>
      ))}

      {/* training-reported (dashed, muted) */}
      {reportedPath && (
        <path
          d={reportedPath}
          fill="none"
          stroke="var(--color-blue, #4a90d9)"
          strokeWidth={1.5}
          strokeDasharray="4 3"
          opacity={0.7}
        />
      )}

      {/* held-out (solid, accent) */}
      <path
        d={heldoutPath}
        fill="none"
        stroke="var(--color-green, #3aa757)"
        strokeWidth={2}
      />

      {/* best-epoch markers */}
      {bestReportedEpoch != null &&
        (() => {
          const e = epochs.find((x) => x.epoch === bestReportedEpoch);
          if (!e || e.training_reported_f1 == null) return null;
          return (
            <circle
              cx={xOf(bestReportedEpoch)}
              cy={yOf(e.training_reported_f1)}
              r={3}
              fill="none"
              stroke="var(--color-blue, #4a90d9)"
              strokeWidth={1.5}
            />
          );
        })()}
      {bestHeldoutEpoch != null &&
        (() => {
          const e = epochs.find((x) => x.epoch === bestHeldoutEpoch);
          if (!e) return null;
          return (
            <g>
              <circle
                cx={xOf(bestHeldoutEpoch)}
                cy={yOf(e.heldout_f1)}
                r={4}
                fill="var(--color-green, #3aa757)"
              />
              <text
                x={xOf(bestHeldoutEpoch)}
                y={yOf(e.heldout_f1) - 7}
                textAnchor="middle"
                fontSize={9}
                fontWeight={700}
                fill="var(--color-green, #3aa757)"
              >
                BEST
              </text>
            </g>
          );
        })()}

      {/* selection scrub line + dot */}
      {selectedEntry && (
        <g>
          <line
            x1={xOf(selectedEntry.epoch as number)}
            x2={xOf(selectedEntry.epoch as number)}
            y1={PAD_T}
            y2={VB_H - PAD_B}
            stroke="var(--color-text-muted, #bbb)"
            strokeWidth={1}
            strokeDasharray="2 2"
          />
          <circle
            cx={xOf(selectedEntry.epoch as number)}
            cy={yOf(selectedEntry.heldout_f1)}
            r={3}
            fill="var(--color-background, #fff)"
            stroke="var(--color-green, #3aa757)"
            strokeWidth={2}
          />
        </g>
      )}

      {/* x-axis epoch ticks (first, best, last) */}
      {Array.from(
        new Set(
          [minEpoch, bestHeldoutEpoch ?? maxEpoch, maxEpoch].filter(
            (v): v is number => v != null
          )
        )
      ).map((ep) => (
        <text
          key={ep}
          x={xOf(ep)}
          y={VB_H - 8}
          textAnchor="middle"
          fontSize={9}
          fill="var(--color-text-muted, #aaa)"
        >
          {ep}
        </text>
      ))}
      <text
        x={(VB_W + PAD_L) / 2}
        y={VB_H - 0.5}
        textAnchor="middle"
        fontSize={8}
        fill="var(--color-text-muted, #bbb)"
      >
        epoch
      </text>
    </svg>
  );
};

// ---------------------------------------------------------------------------
// Receipt scrubber: render predicted-label boxes for the selected epoch
// ---------------------------------------------------------------------------

interface ScrubberProps {
  job: string;
  selectedEntry: EpochEvaluationEntry | null;
  showcaseKey: string | null;
}

const ReceiptScrubber: React.FC<ScrubberProps> = ({
  job,
  selectedEntry,
  showcaseKey,
}) => {
  const formatSupport = useImageFormatSupport();
  const [cache, setCache] = useState<
    Record<string, EpochEvaluationReceiptRecord>
  >({});
  const [loadingKey, setLoadingKey] = useState<string | null>(null);

  const epochFolder =
    selectedEntry == null
      ? null
      : selectedEntry.epoch != null
        ? String(selectedEntry.epoch)
        : selectedEntry.checkpoint;

  // showcaseKey is "image_id_receiptId"; image_id is a UUID (no underscores).
  const receiptPath = useMemo(() => {
    if (!showcaseKey || epochFolder == null) return null;
    const i = showcaseKey.lastIndexOf("_");
    if (i < 0) return null;
    const img = showcaseKey.slice(0, i);
    const rid = showcaseKey.slice(i + 1);
    return `receipts/epoch-${epochFolder}/receipt-${img}-${rid}.json`;
  }, [showcaseKey, epochFolder]);

  useEffect(() => {
    if (!receiptPath || cache[receiptPath]) return;
    let cancelled = false;
    setLoadingKey(receiptPath);
    api
      .fetchEpochEvaluationReceipt(job, receiptPath)
      .then((rec) => {
        if (!cancelled) setCache((c) => ({ ...c, [receiptPath]: rec }));
      })
      .catch(() => {
        /* missing showcase for this epoch — leave blank */
      })
      .finally(() => {
        if (!cancelled) setLoadingKey(null);
      });
    return () => {
      cancelled = true;
    };
  }, [receiptPath, job, cache]);

  const record = receiptPath ? cache[receiptPath] : null;

  const imageUrl = useMemo(() => {
    if (!record || !formatSupport) return null;
    return getBestImageUrl(record.original.receipt, formatSupport);
  }, [record, formatSupport]);

  // Map (line_id, word_id) -> word bbox for joining predictions to geometry.
  const wordBox = useMemo(() => {
    const m = new Map<string, EpochEvaluationReceiptRecord["original"]["words"][number]>();
    if (record) {
      for (const w of record.original.words) {
        m.set(`${w.line_id}:${w.word_id}`, w);
      }
    }
    return m;
  }, [record]);

  if (!showcaseKey) return null;

  const W = record?.original.receipt.width ?? 1;
  const H = record?.original.receipt.height ?? 1;

  return (
    <div className={styles.scrubber}>
      <div className={styles.scrubberHead}>
        <span>
          Predictions on a held-out receipt
          {selectedEntry?.epoch != null ? ` @ epoch ${selectedEntry.epoch}` : ""}
        </span>
        {record && (
          <span>{Math.round(record.inference_time_ms)}&nbsp;ms</span>
        )}
      </div>
      <div className={styles.receiptFrame}>
        {imageUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img className={styles.receiptImg} src={imageUrl} alt="Receipt" />
        ) : (
          <div className={styles.loading}>
            {loadingKey ? "Loading…" : "—"}
          </div>
        )}
        {record && (
          <svg
            className={styles.overlay}
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
          >
            {record.original.predictions.map((p, idx) => {
              if (!p.predicted_label_base || p.predicted_label_base === "O")
                return null;
              const w =
                p.word_id != null
                  ? wordBox.get(`${p.line_id}:${p.word_id}`)
                  : undefined;
              if (!w) return null;
              const b = w.bounding_box;
              const x = b.x * W;
              const y = (1 - b.y - b.height) * H;
              const color = labelColor(p.predicted_label_base);
              return (
                <rect
                  key={idx}
                  x={x}
                  y={y}
                  width={b.width * W}
                  height={b.height * H}
                  fill={color}
                  fillOpacity={0.22}
                  stroke={color}
                  strokeWidth={Math.max(1, W / 400)}
                  strokeOpacity={p.is_correct ? 0.95 : 0.5}
                  strokeDasharray={p.is_correct ? undefined : `${W / 90} ${W / 120}`}
                />
              );
            })}
          </svg>
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

interface EpochEvaluationProps {
  /** Optional explicit job; otherwise the first available job is used. */
  job?: string;
}

const fmtPct = (v: number | null | undefined): string =>
  v == null ? "—" : `${(v * 100).toFixed(1)}%`;

const EpochEvaluation: React.FC<EpochEvaluationProps> = ({ job }) => {
  const { ref, inView } = useInView({ triggerOnce: true, rootMargin: "200px" });
  const [data, setData] = useState<EpochEvaluationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedEpoch, setSelectedEpoch] = useState<number | null>(null);
  const fetched = useRef(false);

  useEffect(() => {
    if (!inView || fetched.current) return;
    fetched.current = true;
    (async () => {
      try {
        let jobName = job;
        if (!jobName) {
          const { jobs } = await api.fetchEpochEvaluationJobs();
          if (!jobs.length) {
            setError("No epoch evaluations available yet.");
            return;
          }
          jobName = jobs[0];
        }
        const resp = await api.fetchEpochEvaluation(jobName);
        setData(resp);
        setSelectedEpoch(resp.best_epoch_heldout ?? null);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load.");
      }
    })();
  }, [inView, job]);

  // Epochs with a real epoch number, sorted — the curve excludes the
  // synthetic "best" alias (epoch null).
  const epochs = useMemo(
    () =>
      (data?.epochs ?? [])
        .filter((e) => e.epoch != null)
        .sort((a, b) => (a.epoch as number) - (b.epoch as number)),
    [data]
  );

  const selectedEntry = useMemo(
    () => epochs.find((e) => e.epoch === selectedEpoch) ?? null,
    [epochs, selectedEpoch]
  );

  if (error) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.notice}>{error}</div>
      </div>
    );
  }

  if (!data || epochs.length === 0) {
    return (
      <div ref={ref} className={styles.container}>
        <div className={styles.loading}>Loading epoch evaluation…</div>
      </div>
    );
  }

  const perLabel = selectedEntry
    ? Object.entries(selectedEntry.per_label_f1)
        .filter(([k]) => !isAggregate(k))
        .sort((a, b) => b[1] - a[1])
    : [];

  return (
    <div ref={ref} className={styles.container}>
      <div className={styles.header}>
        <span className={styles.title}>
          Which epoch actually generalizes best?
        </span>
        <span className={styles.jobName}>{data.job_name}</span>
        {data.compute?.device === "cuda" && (
          <span className={styles.computeBadge}>
            ⚡ GPU{data.compute.instance_type
              ? ` · ${data.compute.instance_type}`
              : data.compute.gpu_name
                ? ` · ${data.compute.gpu_name}`
                : ""}
          </span>
        )}
      </div>

      <div className={styles.legend}>
        <span className={styles.legendItem}>
          <span
            className={styles.swatch}
            style={{ borderTopColor: "var(--color-green, #3aa757)" }}
          />
          Held-out (re-scored on frozen val set)
        </span>
        <span className={styles.legendItem}>
          <span
            className={styles.swatch}
            style={{
              borderTopColor: "var(--color-blue, #4a90d9)",
              borderTopStyle: "dashed",
            }}
          />
          Training-reported
        </span>
      </div>

      <div className={styles.chartWrap}>
        <EpochCurveChart
          epochs={epochs}
          bestHeldoutEpoch={data.best_epoch_heldout}
          bestReportedEpoch={data.best_epoch_training_reported}
          selectedEpoch={selectedEpoch}
          onSelect={setSelectedEpoch}
        />
      </div>

      {selectedEntry && (
        <div className={styles.detail}>
          <div className={styles.metric}>
            <span className={styles.metricLabel}>Epoch</span>
            <span className={styles.metricValue}>{selectedEntry.epoch}</span>
            <span className={styles.metricSub}>
              {selectedEntry.epoch === data.best_epoch_heldout
                ? "best held-out"
                : `best is ${data.best_epoch_heldout}`}
            </span>
          </div>
          <div className={styles.metric}>
            <span className={styles.metricLabel}>Held-out F1</span>
            <span className={styles.metricValue}>
              {fmtPct(selectedEntry.heldout_f1)}
            </span>
            <span className={styles.metricSub}>
              P {fmtPct(selectedEntry.heldout_precision)} · R{" "}
              {fmtPct(selectedEntry.heldout_recall)}
            </span>
          </div>
          <div className={styles.metric}>
            <span className={styles.metricLabel}>Training-reported</span>
            <span className={styles.metricValue}>
              {fmtPct(selectedEntry.training_reported_f1)}
            </span>
            <span className={styles.metricSub}>
              {selectedEntry.training_reported_f1 != null
                ? `${(
                    (selectedEntry.training_reported_f1 -
                      selectedEntry.heldout_f1) *
                    100
                  ).toFixed(1)} pt gap`
                : "—"}
            </span>
          </div>
          <div className={styles.metric}>
            <span className={styles.metricLabel}>Token accuracy</span>
            <span className={styles.metricValue}>
              {fmtPct(selectedEntry.token_accuracy)}
            </span>
            <span className={styles.metricSub}>
              {selectedEntry.num_receipts_evaluated} receipts
            </span>
          </div>
          <div className={styles.metric}>
            <span className={styles.metricLabel}>Inference</span>
            <span className={styles.metricValue}>
              {selectedEntry.avg_inference_ms != null
                ? `${selectedEntry.avg_inference_ms.toFixed(0)} ms`
                : "—"}
            </span>
            <span className={styles.metricSub}>
              {data.compute?.device === "cuda"
                ? `per receipt · GPU${
                    data.compute.gpu_name ? ` · ${data.compute.gpu_name}` : ""
                  }`
                : `per receipt${
                    data.compute?.device
                      ? ` · ${data.compute.device.toUpperCase()}`
                      : ""
                  }`}
            </span>
          </div>

          {perLabel.length > 0 && (
            <div className={styles.perLabel} style={{ gridColumn: "1 / -1" }}>
              <div className={styles.perLabelTitle}>
                Per-label held-out F1
              </div>
              <div className={styles.bars}>
                {perLabel.map(([label, f1]) => (
                  <div key={label} className={styles.barRow}>
                    <span className={styles.barName}>
                      {formatLabel(label)}
                    </span>
                    <span className={styles.barTrack}>
                      <span
                        className={styles.barFill}
                        style={{
                          width: `${Math.round(f1 * 100)}%`,
                          background: labelColor(label),
                        }}
                      />
                    </span>
                    <span className={styles.barValue}>
                      {(f1 * 100).toFixed(0)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <ReceiptScrubber
        job={data.job_name}
        selectedEntry={selectedEntry}
        showcaseKey={data.showcase_receipt_keys[0] ?? null}
      />

      {!data.val_receipts_hash_verified && (
        <div className={styles.notice}>
          <span className={styles.warnFlag}>⚠ </span>
          Evaluated on the current reconstructed held-out set — the exact
          training-time split couldn&apos;t be verified (data drift since the
          run). Epoch comparison is still apples-to-apples.
        </div>
      )}
    </div>
  );
};

export default EpochEvaluation;
