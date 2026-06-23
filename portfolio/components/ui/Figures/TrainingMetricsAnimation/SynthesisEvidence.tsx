import React from "react";
import { TrainingSynthesisSummary } from "../../../../types/api";
import styles from "./TrainingMetricsAnimation.module.css";
import {
  buildMerchantQualityRows,
  formatCount,
  formatEvidenceCheck,
  formatFieldName,
  formatPercent,
  formatReadinessStatus,
  formatSimilarity,
  reportMerchantSourceQuality,
  sourceQualityMerchantMap,
  summarizeCandidateQuality,
  summarizeCategoryCounts,
  summarizeContractCoverage,
  summarizeExampleGrounding,
  summarizeFieldReplacementCounts,
  summarizeLayoutIntegrity,
  summarizeLlmExecution,
  summarizeMerchantGaps,
  summarizeMerchantOperationGaps,
  summarizeMixBalance,
  summarizeOperationCounts,
  summarizeOperationReadiness,
  summarizeRealBaselineComparison,
  summarizeReceiptPreview,
  summarizeReceiptShape,
  summarizeSelectionEvidence,
  summarizeSourceLineage,
  summarizeSourceQualityMerchant,
  summarizeSourceReceiptQuality,
  summarizeStructureComponentGate,
  summarizeStructureEvidence,
  summarizeSyntheticRejections,
  summarizeTrainingBatchPolicy,
} from "./synthesisSummaries";

export interface SynthesisEvidenceStripProps {
  synthesis?: TrainingSynthesisSummary | null;
}

export const SynthesisMerchantQualityPanel: React.FC<SynthesisEvidenceStripProps> = ({
  synthesis,
}) => {
  if (!synthesis) return null;
  const rows = buildMerchantQualityRows(synthesis);
  if (!rows.length) return null;
  const sourceByMerchant = sourceQualityMerchantMap(synthesis);

  return (
    <div
      className={styles.synthesisMerchantQuality}
      aria-label="Synthetic merchant quality"
    >
      {rows.slice(0, 3).map((merchant) => {
        const merchantName = merchant.merchant_name || "Unknown merchant";
        const accepted = merchant.accepted_count ?? 0;
        const candidateCount = merchant.candidate_count ?? 0;
        const rejected = merchant.rejected_count ?? 0;
        const firstExample = (merchant.accepted_examples || [])[0];
        const operation = firstExample?.operation;
        const previewLine =
          operation === "remove_line_item" && firstExample?.changed_text
            ? firstExample.changed_text
            : firstExample?.preview_lines?.find((line) => line.synthetic_insert)
                ?.text ||
              firstExample?.preview_lines?.[0]?.text ||
              firstExample?.changed_text ||
              (firstExample?.label && firstExample?.field_replacement?.new_text
                ? `${formatFieldName(firstExample.label)} ${firstExample.field_replacement.new_text}`
                : null);
        const checks = (firstExample?.accuracy_checks || [])
          .slice(0, 2)
          .map(formatEvidenceCheck);
        const grounding = summarizeExampleGrounding(firstExample);
        const shapeSummary = summarizeStructureEvidence(
          firstExample?.structure_evidence
        );
        const layoutSummary = summarizeLayoutIntegrity(
          firstExample?.layout_integrity
        );
        const selectionSummary = summarizeSelectionEvidence(firstExample);
        const receiptShapeSummary = summarizeReceiptShape(firstExample);
        const evidenceTitle = [
          receiptShapeSummary.title,
          layoutSummary.title,
          shapeSummary.title,
          selectionSummary.title,
        ]
          .filter(Boolean)
          .join(" | ");
        const evidenceDetail = [
          grounding,
          receiptShapeSummary.label,
          layoutSummary.label,
          shapeSummary.label,
          selectionSummary.label,
          ...checks,
        ]
          .filter((value): value is string => Boolean(value))
          .slice(0, 3)
          .join(" · ");
        const rejectionTitle = summarizeSyntheticRejections(
          merchant.rejection_reasons,
          rejected
        );
        const operationSummary = summarizeOperationCounts(
          merchant.accepted_operation_counts,
          merchant.contract_ready_operations
        );
        const operationGapSummary = summarizeMerchantOperationGaps(merchant);
        const status = merchant.readiness_status || "unknown";
        const sourceSummary = summarizeSourceQualityMerchant(
          sourceByMerchant.get(merchantName) || reportMerchantSourceQuality(merchant)
        );

        return (
          <div
            key={merchantName}
            className={styles.synthesisMerchantCard}
            data-status={status}
          >
            <div className={styles.synthesisMerchantHeader}>
              <strong>{merchantName}</strong>
              <span>{formatReadinessStatus(status)}</span>
            </div>
            <div className={styles.synthesisMerchantMeta}>
              {sourceSummary && (
                <span title={sourceSummary.title}>{sourceSummary.label}</span>
              )}
              <span>
                {formatCount(accepted)}
                {candidateCount ? ` / ${formatCount(candidateCount)}` : ""} accepted
              </span>
              <span>{formatSimilarity(merchant.accepted_structure_similarity?.avg)} sim</span>
              {rejected > 0 && (
                <span title={rejectionTitle.title}>
                  {formatCount(rejected)} rejected
                </span>
              )}
            </div>
            <div className={styles.synthesisMerchantOps}>{operationSummary}</div>
            {operationGapSummary && (
              <div
                className={styles.synthesisMerchantGap}
                title={operationGapSummary.title}
              >
                {operationGapSummary.label}
              </div>
            )}
            {previewLine && (
              <div
                className={styles.synthesisMerchantEvidence}
                title={evidenceTitle}
              >
                <span>{previewLine}</span>
                {evidenceDetail && <em>{evidenceDetail}</em>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export const SynthesisEvidenceStrip: React.FC<SynthesisEvidenceStripProps> = ({
  synthesis,
}) => {
  if (!synthesis) return null;

  const candidateCount = synthesis.candidate_count ?? 0;
  const groundedCount =
    synthesis.accepted_grounded_candidate_count ??
    synthesis.grounded_candidate_count ??
    0;
  const acceptedSynthetic =
    synthesis.bundle_candidates_accepted ??
    synthesis.synthetic_candidates_accepted ??
    synthesis.synthetic_train_examples ??
    null;
  const seenSynthetic =
    synthesis.bundle_candidates_seen ?? synthesis.synthetic_candidates_seen ?? null;
  const rejectedSynthetic =
    synthesis.bundle_candidates_rejected ??
    synthesis.synthetic_candidates_rejected ??
    synthesis.rejected_count ??
    0;
  const groundedShare =
    synthesis.grounded_candidate_share ??
    (candidateCount ? groundedCount / candidateCount : null);
  const acceptedMerchantCount = synthesis.accepted_merchant_count ?? null;
  const readyMerchantCount =
    acceptedMerchantCount ??
    synthesis.ready_merchant_count ??
    synthesis.readiness_status_counts?.ready ??
    null;
  const blockedMerchantCount =
    synthesis.readiness_status_counts?.blocked ??
    synthesis.candidate_mix_merchants?.filter(
      (merchant) =>
        (merchant.rejection_reasons?.merchant_synthesis_not_ready ?? 0) > 0
    ).length ??
    0;
  const merchantEvidenceLabel =
    acceptedMerchantCount != null ? "Accepted merchants" : "Ready merchants";
  const readinessTotal = synthesis.merchant_count ?? null;
  const avgReadinessScore = synthesis.avg_readiness_score ?? null;
  const merchantEvidenceDetails = [
    blockedMerchantCount ? `${formatCount(blockedMerchantCount)} blocked` : null,
    acceptedMerchantCount == null && avgReadinessScore
      ? formatPercent(avgReadinessScore)
      : null,
  ].filter((value): value is string => Boolean(value));
  const arithmeticUpdateCount = Object.values(
    synthesis.arithmetic_update_counts || {}
  ).reduce((sum, count) => sum + count, 0);
  const arithmeticCandidateCount =
    synthesis.accepted_arithmetic_candidate_count ??
    synthesis.arithmetic_candidate_count ??
    0;
  const hasArtifact = synthesis.status === "available";
  const statusLabel = hasArtifact ? "Artifact" : "Metrics";
  const rejectionSummary = rejectedSynthetic
    ? summarizeSyntheticRejections(
        synthesis.bundle_rejection_reasons ??
          synthesis.synthetic_rejection_reasons ??
          synthesis.rejection_reasons,
        rejectedSynthetic
      )
    : null;
  const categorySummary = summarizeCategoryCounts(
    synthesis.accepted_category_counts ?? synthesis.category_counts
  );
  const fieldReplacementSummary = summarizeFieldReplacementCounts(
    synthesis.accepted_field_replacement_counts ??
      synthesis.field_replacement_counts,
    synthesis.candidate_examples
  );
  const candidateQualitySummary = summarizeCandidateQuality(synthesis);
  const sourceLineageSummary = summarizeSourceLineage(synthesis);
  const merchantGapSummary = summarizeMerchantGaps(synthesis);
  const operationReadinessSummary = summarizeOperationReadiness(synthesis);
  const sourceReceiptQualitySummary = summarizeSourceReceiptQuality(synthesis);
  const contractSummary = summarizeContractCoverage(synthesis);
  const llmExecutionSummary = summarizeLlmExecution(synthesis);
  const receiptPreviewSummary = summarizeReceiptPreview(
    synthesis.candidate_examples
  );
  const structureGateSummary = summarizeStructureComponentGate(synthesis);
  const realBaselineSummary = summarizeRealBaselineComparison(
    synthesis.accepted_real_baseline_comparison ??
      synthesis.synthetic_accepted_real_baseline_comparison ??
      synthesis.quality_report?.summary?.accepted_real_baseline_comparison
  );
  const mixBalanceSummary = summarizeMixBalance(
    synthesis.accepted_mix_balance ??
      synthesis.synthetic_accepted_mix_balance ??
      synthesis.quality_report?.summary?.accepted_mix_balance
  );
  const trainingBatchPolicySummary = summarizeTrainingBatchPolicy(
    synthesis.quality_report?.training_batch_policy
  );

  return (
    <div
      className={styles.synthesisEvidencePanel}
      aria-label="Synthetic receipt grounding evidence"
    >
      <div className={styles.synthesisEvidenceStrip}>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={rejectionSummary?.title}>
            {rejectionSummary ? rejectionSummary.label : statusLabel}
          </span>
          <strong>
            {formatCount(acceptedSynthetic)}
            {seenSynthetic ? ` / ${formatCount(seenSynthetic)}` : ""} synth
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>Grounded</span>
          <strong>
            {formatCount(groundedCount)}
            {candidateCount ? ` / ${formatCount(candidateCount)}` : ""}
            {candidateCount ? ` (${formatPercent(groundedShare)})` : ""}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={candidateQualitySummary?.title}>Fidelity</span>
          <strong title={candidateQualitySummary?.title}>
            {candidateQualitySummary?.label ?? "—"}
          </strong>
        </div>
        <div
          className={`${styles.synthesisEvidenceMetric}${
            sourceLineageSummary?.warning
              ? ` ${styles.synthesisEvidenceMetricWarning}`
              : ""
          }`}
        >
          <span title={sourceLineageSummary?.title}>
            {sourceLineageSummary?.statusLabel ?? "Lineage"}
          </span>
          <strong title={sourceLineageSummary?.title}>
            {sourceLineageSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>{merchantEvidenceLabel}</span>
          <strong>
            {formatCount(readyMerchantCount)}
            {readinessTotal ? ` / ${formatCount(readinessTotal)}` : ""}
            {merchantEvidenceDetails.length
              ? ` (${merchantEvidenceDetails.join(", ")})`
              : ""}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={merchantGapSummary?.title}>Merchant gaps</span>
          <strong title={merchantGapSummary?.title}>
            {merchantGapSummary?.label ?? "—"}
          </strong>
        </div>
        <div
          className={`${styles.synthesisEvidenceMetric}${
            operationReadinessSummary?.warning
              ? ` ${styles.synthesisEvidenceMetricWarning}`
              : ""
          }`}
        >
          <span title={operationReadinessSummary?.title}>Operation readiness</span>
          <strong title={operationReadinessSummary?.title}>
            {operationReadinessSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={mixBalanceSummary?.title}>Mix balance</span>
          <strong title={mixBalanceSummary?.title}>
            {mixBalanceSummary?.label ?? "—"}
          </strong>
        </div>
        <div
          className={`${styles.synthesisEvidenceMetric}${
            trainingBatchPolicySummary?.warning
              ? ` ${styles.synthesisEvidenceMetricWarning}`
              : ""
          }`}
        >
          <span title={trainingBatchPolicySummary?.title}>Batch policy</span>
          <strong title={trainingBatchPolicySummary?.title}>
            {trainingBatchPolicySummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={sourceReceiptQualitySummary?.title}>Source receipts</span>
          <strong title={sourceReceiptQualitySummary?.title}>
            {sourceReceiptQualitySummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>Contracts</span>
          <strong title={contractSummary?.title}>
            {contractSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={llmExecutionSummary?.title}>LLM mode</span>
          <strong title={llmExecutionSummary?.title}>
            {llmExecutionSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={structureGateSummary?.title}>Avg similarity</span>
          <strong title={structureGateSummary?.title}>
            {formatSimilarity(
              synthesis.accepted_structure_similarity?.avg ??
                synthesis.avg_structure_similarity ??
                synthesis.best_structure_similarity
            )}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={realBaselineSummary?.title}>Real baseline</span>
          <strong title={realBaselineSummary?.title}>
            {realBaselineSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span title={structureGateSummary?.title}>Geometry gate</span>
          <strong title={structureGateSummary?.title}>
            {structureGateSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>Receipt preview</span>
          <strong title={receiptPreviewSummary?.title}>
            {receiptPreviewSummary?.label ?? categorySummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>Field edits</span>
          <strong title={fieldReplacementSummary?.title}>
            {fieldReplacementSummary?.label ?? "—"}
          </strong>
        </div>
        <div className={styles.synthesisEvidenceMetric}>
          <span>Arithmetic</span>
          <strong>
            {formatCount(arithmeticCandidateCount)} edits
            {arithmeticUpdateCount
              ? ` / ${formatCount(arithmeticUpdateCount)} rows`
              : ""}
          </strong>
        </div>
      </div>
      <SynthesisMerchantQualityPanel synthesis={synthesis} />
    </div>
  );
};
