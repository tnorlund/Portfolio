import {
  TrainingSynthesisAcceptedSourceLineage,
  TrainingSynthesisCandidateQuality,
  TrainingSynthesisLayoutIntegrityEvidence,
  TrainingSynthesisMerchantGapSummary,
  TrainingSynthesisMixBalance,
  TrainingSynthesisQualityMerchant,
  TrainingSynthesisRealBaselineSummary,
  TrainingSynthesisSourceQualityMerchant,
  TrainingSynthesisStructureEvidence,
  TrainingSynthesisSummary,
  TrainingSynthesisTrainingBatchPolicy,
} from "../../../../types/api";

export const formatCount = (value?: number | null): string =>
  Number.isFinite(value) ? (value as number).toLocaleString() : "—";

export const formatSimilarity = (value?: number | null): string =>
  Number.isFinite(value) ? (value as number).toFixed(2) : "—";

export const formatPercent = (value?: number | null): string =>
  Number.isFinite(value) ? `${Math.round((value as number) * 100)}%` : "—";

export const SYNTHETIC_REJECTION_REASON_LABELS: Record<string, string> = {
  add_item_base_category_missing: "wrong section",
  add_item_catalog_category_mismatch: "catalog category mismatch",
  add_item_catalog_missing_category_evidence: "catalog missing category",
  add_item_catalog_not_cross_receipt_grounded: "catalog ungrounded item",
  add_item_category_mismatch: "category mismatch",
  add_item_missing_category_evidence: "missing category",
  add_item_not_cross_receipt_grounded: "ungrounded item",
  add_item_placement_base_category_missing: "placement missing section",
  add_item_placement_category_mismatch: "placement category mismatch",
  accepted_synthetic_mix_high_risk: "high-risk merchant mix",
  accepted_synthetic_mix_single_merchant_high_risk: "single merchant mix",
  accepted_synthetic_mix_top_merchant_high_risk: "top merchant dominates",
  bundle_not_ready: "bundle not ready",
  bundle_training_not_ready: "bundle training hold",
  invalid_arithmetic_reconciliation: "bad arithmetic",
  below_real_structure_baseline: "below real baseline",
  low_category_sequence_similarity: "weak category order",
  low_category_set_similarity: "weak category match",
  low_line_step_similarity: "weak row spacing",
  low_price_column_similarity: "weak price column",
  low_structure_similarity: "low similarity",
  low_token_count_similarity: "weak token count",
  merchant_operation_synthetic_cap: "operation cap",
  merchant_synthesis_not_ready: "merchant not ready",
  merchant_synthetic_cap: "merchant cap",
  missing_arithmetic_reconciliation: "missing arithmetic",
  missing_base_receipt_lineage: "missing base receipt",
  missing_metadata: "missing metadata",
  missing_structure_similarity: "missing similarity",
  replace_field_format_mismatch: "format mismatch",
  replace_field_insufficient_observations: "few field examples",
  replace_field_invalid_value: "bad field value",
  replace_field_label_mismatch: "label mismatch",
  replace_field_missing_evidence: "missing field evidence",
  replace_field_missing_format: "missing field format",
  replace_field_not_mutable: "field not mutable",
  replace_field_unsupported_label: "unsupported field",
  replace_field_unstable_geometry: "unstable field geometry",
  single_merchant_accepted: "single merchant accepted",
  top_merchant_share_ge_80pct: "top merchant >=80%",
};

export const formatSyntheticRejectionReason = (reason: string): string =>
  SYNTHETIC_REJECTION_REASON_LABELS[reason] ||
  reason.replaceAll("_", " ");

export const summarizeSyntheticRejections = (
  reasons: Record<string, number> | undefined,
  rejectedCount: number
): { label: string; title?: string } => {
  const entries = Object.entries(reasons || {})
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
  const title = entries.length
    ? entries
        .map(
          ([reason, count]) =>
            `${formatCount(count)} ${formatSyntheticRejectionReason(reason)}`
        )
        .join(", ")
    : undefined;
  const cappedCount = entries.reduce(
    (sum, [reason, count]) =>
      reason === "merchant_synthetic_cap" ||
      reason === "merchant_operation_synthetic_cap"
        ? sum + count
        : sum,
    0
  );
  if (cappedCount > 0) {
    const label =
      cappedCount === rejectedCount
        ? `${formatCount(rejectedCount)} capped`
        : `${formatCount(rejectedCount)} rejected (${formatCount(cappedCount)} capped)`;
    return { label, title };
  }
  return { label: `${formatCount(rejectedCount)} rejected`, title };
};

export const formatCategoryName = (category: string): string =>
  category
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join(" ");

export const formatFieldName = (field: string): string => formatCategoryName(field);

export const formatOperationName = (operation: string): string =>
  operation === "replace_field"
    ? "Field edits"
    : formatCategoryName(operation);

export type TrainingSynthesisMerchantGap = NonNullable<
  TrainingSynthesisMerchantGapSummary["merchants"]
>[number];

export const summarizeCategoryCounts = (
  counts: Record<string, number> | undefined
): { label: string; title?: string } | null => {
  const entries = Object.entries(counts || {})
    .filter(([category, count]) => category && count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!entries.length) return null;
  const [topCategory, topCount] = entries[0];
  return {
    label: `${formatCategoryName(topCategory)}: ${formatCount(topCount)}`,
    title: entries
      .map(([category, count]) => `${formatCategoryName(category)}: ${formatCount(count)}`)
      .join(", "),
  };
};

export const summarizeFieldReplacementCounts = (
  counts: Record<string, number> | undefined,
  examples: TrainingSynthesisSummary["candidate_examples"] | undefined
): { label: string; title?: string } | null => {
  const merged = new Map<string, number>();
  Object.entries(counts || {}).forEach(([field, count]) => {
    if (field && count > 0) merged.set(field, count);
  });
  (examples || []).forEach((example) => {
    if (example.operation !== "replace_field" || !example.field_label) return;
    if (!merged.has(example.field_label)) {
      merged.set(example.field_label, 1);
    }
  });

  const entries = Array.from(merged.entries()).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
  );
  if (!entries.length) return null;

  const examplesByField = new Map(
    (examples || [])
      .filter((example) => example.field_label)
      .map((example) => [example.field_label as string, example])
  );
  const label = entries
    .slice(0, 2)
    .map(([field, count]) => `${formatFieldName(field)}: ${formatCount(count)}`)
    .join(", ");
  const title = entries
    .map(([field, count]) => {
      const example = examplesByField.get(field);
      const replacement =
        example?.old_text && example?.new_text
          ? ` (${example.old_text} -> ${example.new_text}${
              example.field_format ? `, ${example.field_format}` : ""
            })`
          : "";
      return `${formatFieldName(field)}: ${formatCount(count)}${replacement}`;
    })
    .join(", ");

  return {
    label:
      entries.length > 2
        ? `${label}, +${formatCount(entries.length - 2)}`
        : label,
    title,
  };
};

export const summarizeContractCoverage = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const contracts = synthesis.merchant_synthesis_contracts || [];
  const total =
    synthesis.contract_merchant_count ??
    (contracts.length ? contracts.length : null);
  const ready =
    synthesis.contract_ready_merchant_count ??
    (contracts.length
      ? contracts.filter((contract) => contract.status === "ready").length
      : null);
  const operationEntries = Object.entries(synthesis.contract_operation_counts || {})
    .filter(([operation, count]) => operation && count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const operationCoverage = synthesis.quality_report?.operation_coverage;
  const acceptedOperationCoverage =
    synthesis.quality_report?.accepted_operation_coverage ??
    synthesis.accepted_operation_coverage ??
    synthesis.synthetic_accepted_operation_coverage;

  if (total == null && !operationEntries.length && !acceptedOperationCoverage) {
    return null;
  }

  const operationTitle = operationEntries
    .map(
      ([operation, count]) =>
        `${formatOperationName(operation)}: ${formatCount(count)}`
    )
    .join(", ");
  const readyOperations = operationCoverage?.ready_operation_count;
  const totalOperations = operationCoverage?.operation_count;
  const coverageTitle =
    readyOperations != null && totalOperations
      ? `Operations ready: ${formatCount(readyOperations)} / ${formatCount(totalOperations)}`
      : null;
  const acceptedReadyOperations =
    acceptedOperationCoverage?.accepted_ready_operation_count;
  const readyAcceptedTotal = acceptedOperationCoverage?.ready_operation_count;
  const acceptedCoverageTitle =
    acceptedReadyOperations != null && readyAcceptedTotal
      ? `Ready ops accepted: ${formatCount(acceptedReadyOperations)} / ${formatCount(readyAcceptedTotal)}`
      : null;
  const uncoveredTitle = (
    acceptedOperationCoverage?.uncovered_ready_operations || []
  ).length
    ? `Uncovered ready ops: ${(
        acceptedOperationCoverage?.uncovered_ready_operations || []
      )
        .map(formatOperationName)
        .join(", ")}`
    : null;
  const trainingReadyTitle =
    synthesis.quality_report?.training_ready === false
      ? `Training hold: ${(synthesis.quality_report.training_ready_reasons || [])
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null;
  return {
    label:
      total != null
        ? `${formatCount(ready ?? 0)} / ${formatCount(total)} ready`
        : acceptedReadyOperations != null && readyAcceptedTotal != null
          ? `${formatCount(acceptedReadyOperations)} / ${formatCount(readyAcceptedTotal)} accepted`
        : "—",
    title: [
      operationTitle,
      coverageTitle,
      acceptedCoverageTitle,
      uncoveredTitle,
      trainingReadyTitle,
    ]
      .filter((value): value is string => Boolean(value))
      .join(" | ") || undefined,
  };
};

export const describeModelGuidanceStatus = (
  freshnessGate:
    | NonNullable<
        NonNullable<TrainingSynthesisSummary["quality_report"]>["quality_gates"]
      >["llm_model_freshness_gate"]
    | undefined,
  verifiedAt?: string | null
): { guidanceStatus: string; latestModelStatus: string } => {
  if (freshnessGate?.requires_current_model_guidance === false) {
    const localStatus = "not required for local-only run";
    return {
      guidanceStatus: localStatus,
      latestModelStatus: verifiedAt
        ? `${localStatus}; verified ${verifiedAt}`
        : localStatus,
    };
  }

  if (freshnessGate?.passed === false) {
    return {
      guidanceStatus: "stale or missing",
      latestModelStatus: "stale or missing",
    };
  }

  if (
    freshnessGate?.requires_current_model_guidance === true &&
    freshnessGate.passed === true
  ) {
    return {
      guidanceStatus: "fresh",
      latestModelStatus: verifiedAt
        ? `verified ${verifiedAt}`
        : "verification date missing",
    };
  }

  if (freshnessGate?.passed === true) {
    return {
      guidanceStatus: "freshness gate passed",
      latestModelStatus: verifiedAt
        ? `freshness gate passed; verified ${verifiedAt}`
        : "freshness gate passed; verification date missing",
    };
  }

  return {
    guidanceStatus: "verification not reported",
    latestModelStatus: verifiedAt ? `verified ${verifiedAt}` : "verification missing",
  };
};

export const summarizeLlmExecution = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const execution =
    synthesis.llm_execution ?? synthesis.quality_report?.summary?.llm_execution;
  if (!execution) return null;

  const entries = Object.entries(execution.mode_counts || {})
    .filter(([mode, count]) => mode && count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const apiAllowed = execution.api_call_allowed_count ?? 0;
  const disabled = execution.paid_llm_disabled_count ?? 0;
  if (!entries.length && !apiAllowed && !disabled) return null;

  const primaryMode = entries[0]?.[0];
  let label = "Unknown";
  if (apiAllowed > 0) {
    label = "LLM assisted";
  } else if (primaryMode === "deterministic_fallback" || disabled > 0) {
    label = "Local only";
  } else if (primaryMode) {
    label = formatFieldName(primaryMode);
  }
  const modeTitle = entries
    .map(([mode, count]) => `${formatFieldName(mode)}: ${formatCount(count)}`)
    .join(", ");
  const freshnessGate = synthesis.quality_report?.quality_gates
    ?.llm_model_freshness_gate;
  const modelTitle = (execution.configured_models || []).length
    ? `Models: ${(execution.configured_models || []).join(", ")}`
    : null;
  const guidanceStatus = describeModelGuidanceStatus(
    freshnessGate,
    execution.latest_model_verified_at
  );
  const latestModelTitle = (execution.latest_openai_models || []).length
    ? `Latest OpenAI guidance: ${(execution.latest_openai_models || []).join(
        ", "
      )} (${guidanceStatus.latestModelStatus})`
    : null;
  const sourceTitle = (execution.latest_model_sources || []).length
    ? `Latest model source: ${(execution.latest_model_sources || []).join(", ")}`
    : null;
  const verifiedTitle = execution.latest_model_verified_at
    ? `Latest model verified: ${execution.latest_model_verified_at}`
    : null;
  const freshnessTitle = freshnessGate
    ? `Model guidance: ${guidanceStatus.guidanceStatus}${
        freshnessGate.latest_model_age_days != null
          ? ` (${formatCount(freshnessGate.latest_model_age_days)}d old`
          : ""
      }${
        freshnessGate.max_age_days != null
          ? `${freshnessGate.latest_model_age_days != null ? ", " : " ("}max ${formatCount(freshnessGate.max_age_days)}d`
          : ""
      }${
        freshnessGate.latest_model_age_days != null ||
        freshnessGate.max_age_days != null
          ? ")"
          : ""
      }`
    : null;
  const freshnessHoldTitle =
    freshnessGate?.requires_current_model_guidance && freshnessGate.passed === false
      ? `Training hold: ${formatSyntheticRejectionReason(
          freshnessGate.reason || "refresh_latest_model_guidance_before_synthesis"
        )}`
      : null;

  return {
    label,
    title:
      [
        modeTitle,
        `Paid LLM disabled: ${formatCount(disabled)}`,
        `API allowed: ${formatCount(apiAllowed)}`,
        modelTitle,
        latestModelTitle,
        sourceTitle,
        verifiedTitle,
        freshnessTitle,
        freshnessHoldTitle,
      ]
        .filter((value): value is string => Boolean(value))
        .join(" | ") || undefined,
  };
};

export const summarizeCountRecord = (
  counts: Record<string, number> | undefined,
  formatter: (value: string) => string,
  limit = 3
): string | null => {
  const entries = Object.entries(counts || {})
    .filter(([key, count]) => key && count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!entries.length) return null;
  return entries
    .slice(0, limit)
    .map(([key, count]) => `${formatter(key)}: ${formatCount(count)}`)
    .join(", ");
};

export const hasMerchantGap = (merchant: TrainingSynthesisMerchantGap): boolean =>
  merchant.status === "blocked" ||
  Boolean(merchant.blockers?.length) ||
  Boolean(merchant.limitations?.length) ||
  Boolean(merchant.missing_operations?.length);

export const summarizeMerchantGaps = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const summary = synthesis.quality_report?.merchant_gap_summary;
  if (!summary) return null;

  const merchants = summary.merchants || [];
  const derivedGapCount = merchants.filter(hasMerchantGap).length;
  const gapCount = summary.merchant_gap_count ?? derivedGapCount;
  const blockedCount =
    summary.blocked_merchant_count ??
    merchants.filter((merchant) => merchant.status === "blocked").length;
  const total = synthesis.merchant_count ?? (merchants.length || null);
  const topBlockers = summarizeCountRecord(
    summary.top_blockers,
    formatSyntheticRejectionReason
  );
  const topLimitations = summarizeCountRecord(
    summary.top_limitations,
    formatSyntheticRejectionReason
  );
  const missingOperations = Array.from(
    new Set(merchants.flatMap((merchant) => merchant.missing_operations || []))
  ).filter(Boolean);
  const title = [
    topBlockers ? `Blockers: ${topBlockers}` : null,
    topLimitations ? `Limitations: ${topLimitations}` : null,
    missingOperations.length
      ? `Missing ops: ${missingOperations.map(formatOperationName).join(", ")}`
      : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  if (gapCount > 0) {
    return {
      label: `${formatCount(gapCount)}${total ? ` / ${formatCount(total)}` : ""} gaps`,
      title: title || undefined,
    };
  }
  if (blockedCount > 0) {
    return {
      label: `${formatCount(blockedCount)} blocked`,
      title: title || undefined,
    };
  }
  return {
    label: "No gaps",
    title: title || undefined,
  };
};

export const summarizeOperationReadiness = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string; warning: boolean } | null => {
  const merchants = synthesis.quality_report?.merchants || [];
  const operationRows = merchants.flatMap((merchant) =>
    (merchant.operation_readiness || []).map((row) => ({
      merchant: merchant.merchant_name || "Unknown merchant",
      ...row,
    }))
  );
  if (!operationRows.length) return null;

  const supportedRows = operationRows.filter((row) => row.supported);
  const readyRows = supportedRows.filter((row) => row.ready);
  const reusableReadyRows = readyRows.filter(
    (row) => (row.evidence_candidate_count ?? 0) > 0
  );
  const missingOperations = Array.from(
    new Set(merchants.flatMap((merchant) => merchant.missing_operations || []))
  ).filter(Boolean);
  const nextActions = Array.from(
    new Set(merchants.flatMap((merchant) => merchant.next_synthesis_actions || []))
  ).filter(Boolean);
  const nextActionCounts = summarizeCountRecord(
    synthesis.quality_report?.summary?.next_synthesis_action_counts,
    formatSyntheticRejectionReason,
    6
  );
  const actionNeedsAttention = nextActions.some(
    (action) => action.startsWith("collect_") || action.startsWith("resolve_")
  );
  const blockedRows = operationRows
    .filter((row) => !row.ready)
    .slice(0, 5)
    .map((row) => {
      const blockers = (row.blockers || [])
        .slice(0, 2)
        .map(formatSyntheticRejectionReason)
        .join(", ");
      return `${row.merchant} ${formatOperationName(row.operation || "unknown")}${
        blockers ? ` (${blockers})` : ""
      }`;
    });

  const title = [
    `Supported operation rows: ${formatCount(supportedRows.length)} / ${formatCount(operationRows.length)}`,
    `Contract-ready rows: ${formatCount(readyRows.length)} / ${formatCount(supportedRows.length)}`,
    `Ready with reusable evidence: ${formatCount(reusableReadyRows.length)} / ${formatCount(readyRows.length)}`,
    missingOperations.length
      ? `Missing ops: ${missingOperations.map(formatOperationName).join(", ")}`
      : null,
    nextActions.length
      ? `Next actions: ${nextActions
          .slice(0, 4)
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null,
    nextActionCounts ? `Action backlog: ${nextActionCounts}` : null,
    blockedRows.length ? `Blocked rows: ${blockedRows.join(" | ")}` : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  const label = readyRows.length
    ? `${formatCount(reusableReadyRows.length)} / ${formatCount(readyRows.length)} reusable`
    : supportedRows.length
      ? `0 / ${formatCount(supportedRows.length)} ready`
      : "No supported ops";

  return {
    label,
    title: title || undefined,
    warning:
      supportedRows.length === 0 ||
      readyRows.length < supportedRows.length ||
      reusableReadyRows.length < readyRows.length ||
      missingOperations.length > 0 ||
      actionNeedsAttention,
  };
};

export const summarizeReasonList = (
  merchants: TrainingSynthesisSourceQualityMerchant[],
  field: "blockers" | "limitations"
): string | null => {
  const counts = new Map<string, number>();
  merchants.forEach((merchant) => {
    (merchant[field] || []).forEach((reason) => {
      if (!reason) return;
      counts.set(reason, (counts.get(reason) || 0) + 1);
    });
  });
  const entries = Array.from(counts.entries()).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
  );
  if (!entries.length) return null;
  return entries
    .slice(0, 3)
    .map(([reason, count]) => `${formatSyntheticRejectionReason(reason)}: ${formatCount(count)}`)
    .join(", ");
};

export const summarizeSourceLabels = (
  merchants: TrainingSynthesisSourceQualityMerchant[]
): string | null => {
  const counts = new Map<string, number>();
  merchants.forEach((merchant) => {
    Object.entries(merchant.top_labels || {}).forEach(([label, count]) => {
      if (!label || !count) return;
      counts.set(label, (counts.get(label) || 0) + count);
    });
  });
  const entries = Array.from(counts.entries()).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0])
  );
  if (!entries.length) return null;
  return entries
    .slice(0, 4)
    .map(([label, count]) => `${formatFieldName(label)}: ${formatCount(count)}`)
    .join(", ");
};

export const sourceQualityMerchantMap = (
  synthesis: TrainingSynthesisSummary
): Map<string, TrainingSynthesisSourceQualityMerchant> =>
  new Map(
    (synthesis.source_receipt_quality?.merchants || [])
      .filter((merchant) => merchant.merchant_name)
      .map((merchant) => [merchant.merchant_name as string, merchant])
  );

export const reportMerchantSourceQuality = (
  merchant: TrainingSynthesisQualityMerchant
): TrainingSynthesisSourceQualityMerchant | undefined => {
  if (
    !merchant.source_quality_status &&
    merchant.source_quality_receipt_count == null &&
    merchant.source_quality_labeled_word_count == null
  ) {
    return undefined;
  }
  return {
    merchant_name: merchant.merchant_name,
    status: merchant.source_quality_status,
    receipt_count: merchant.source_quality_receipt_count,
    labeled_word_count: merchant.source_quality_labeled_word_count,
    receipts_with_line_item_labels:
      merchant.source_quality_receipts_with_line_item_labels,
    receipts_with_grand_total_label:
      merchant.source_quality_receipts_with_grand_total_label,
    receipts_with_date_or_time_label:
      merchant.source_quality_receipts_with_date_or_time_label,
    text_structure_status: merchant.source_quality_text_structure_status,
    line_item_like_text_line_count:
      merchant.source_quality_line_item_like_text_line_count,
    total_like_text_line_count:
      merchant.source_quality_total_like_text_line_count,
    limitations: Array.from(
      new Set([
        ...(merchant.source_quality_limitations || []),
        ...(merchant.limitations || []),
      ])
    ),
    requires_label_validation:
      merchant.source_quality_requires_label_validation,
    blockers: Array.from(
      new Set(Object.values(merchant.source_quality_operation_blockers || {}))
    ),
  };
};

export const summarizeSourceQualityMerchant = (
  merchant?: TrainingSynthesisSourceQualityMerchant
): { label: string; title?: string } | null => {
  if (!merchant) return null;
  const receiptCount = merchant.receipt_count ?? 0;
  const labeledWordCount = merchant.labeled_word_count ?? 0;
  const lineItemReceipts = merchant.receipts_with_line_item_labels ?? 0;
  const totalReceipts = receiptCount || merchant.receipts_with_labels || 0;
  const labelValidationRequired = (merchant.limitations || []).includes(
    "unlabeled_text_requires_label_validation"
  );
  const recoverableUnlabeled =
    merchant.requires_label_validation === true ||
    merchant.text_structure_status === "recoverable_unlabeled_text" ||
    labelValidationRequired;
  const title = [
    `${formatReadinessStatus(merchant.status)} source receipts`,
    `Receipts: ${formatCount(receiptCount)}`,
    `Labeled words: ${formatCount(labeledWordCount)}`,
    recoverableUnlabeled
      ? `Recoverable OCR: ${formatCount(
          merchant.total_like_text_line_count ?? 0
        )} total-like anchors, ${formatCount(
          merchant.line_item_like_text_line_count ?? 0
        )} line-item-like rows`
      : null,
    `Line item labels: ${formatCount(lineItemReceipts)} / ${formatCount(totalReceipts)}`,
    merchant.receipts_with_grand_total_label != null
      ? `Grand total labels: ${formatCount(merchant.receipts_with_grand_total_label)}`
      : null,
    merchant.receipts_with_date_or_time_label != null
      ? `Date/time labels: ${formatCount(merchant.receipts_with_date_or_time_label)}`
      : null,
    merchant.blockers?.length
      ? `Blockers: ${merchant.blockers.map(formatSyntheticRejectionReason).join(", ")}`
      : null,
    merchant.limitations?.length
      ? `Limitations: ${merchant.limitations.map(formatSyntheticRejectionReason).join(", ")}`
      : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");
  return {
    label: recoverableUnlabeled
      ? `${formatCount(receiptCount)} src · ${formatCount(labeledWordCount)} labels · validate`
      : `${formatCount(receiptCount)} src · ${formatCount(labeledWordCount)} labels`,
    title,
  };
};

export const summarizeSourceReceiptQuality = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const quality = synthesis.source_receipt_quality;
  if (!quality) return null;
  const merchants = quality.merchants || [];
  const merchantCount = quality.merchant_count ?? merchants.length;
  const usable =
    quality.usable_merchant_count ??
    quality.status_counts?.usable ??
    merchants.filter((merchant) => merchant.status === "usable").length;
  const limited =
    quality.limited_merchant_count ??
    quality.status_counts?.limited ??
    merchants.filter((merchant) => merchant.status === "limited").length;
  const blocked =
    quality.blocked_merchant_count ??
    quality.status_counts?.blocked ??
    merchants.filter((merchant) => merchant.status === "blocked").length;
  const receiptCount =
    quality.receipt_count ??
    merchants.reduce((sum, merchant) => sum + (merchant.receipt_count || 0), 0);
  const labeledWordCount =
    quality.labeled_word_count ??
    merchants.reduce(
      (sum, merchant) => sum + (merchant.labeled_word_count || 0),
      0
    );
  const blockers = summarizeReasonList(merchants, "blockers");
  const limitations = summarizeReasonList(merchants, "limitations");
  const labels = summarizeSourceLabels(merchants);
  const title = [
    `Receipts: ${formatCount(receiptCount)}`,
    `Labeled words: ${formatCount(labeledWordCount)}`,
    `Usable: ${formatCount(usable)}`,
    limited ? `Limited: ${formatCount(limited)}` : null,
    blocked ? `Blocked: ${formatCount(blocked)}` : null,
    blockers ? `Blockers: ${blockers}` : null,
    limitations ? `Limitations: ${limitations}` : null,
    labels ? `Top labels: ${labels}` : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");
  return {
    label:
      merchantCount != null
        ? `${formatCount(usable)} / ${formatCount(merchantCount)} usable`
        : `${formatCount(usable)} usable`,
    title,
  };
};

export const summarizeCandidateQuality = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const byId = new Map<string, TrainingSynthesisCandidateQuality>();
  (synthesis.candidate_examples || []).forEach((example, index) => {
    if (example.candidate_quality) {
      byId.set(example.candidate_id || `candidate-${index}`, example.candidate_quality);
    }
  });
  (synthesis.quality_report?.merchants || []).forEach((merchant) => {
    (merchant.accepted_examples || []).forEach((example, index) => {
      if (example.candidate_quality) {
        byId.set(
          example.candidate_id || `${merchant.merchant_name || "merchant"}-${index}`,
          example.candidate_quality
        );
      }
    });
  });
  const qualities = Array.from(byId.values()).filter(
    (quality): quality is TrainingSynthesisCandidateQuality =>
      Boolean(quality) && Number.isFinite(quality.score)
  );
  const aggregate =
    synthesis.accepted_candidate_quality ??
    synthesis.quality_report?.summary?.accepted_candidate_quality;
  const aggregateComponents =
    synthesis.accepted_candidate_quality_components ??
    synthesis.quality_report?.summary?.accepted_candidate_quality_components;

  if (!qualities.length && !aggregate) return null;
  if (!qualities.length && aggregate) {
    const componentTitle = Object.entries(aggregateComponents || {})
      .filter(([, value]) => Number.isFinite(value.avg))
      .sort(
        (a, b) =>
          (a[1].avg as number) - (b[1].avg as number) ||
          a[0].localeCompare(b[0])
      )
      .slice(0, 4)
      .map(
        ([name, value]) =>
          `${formatFieldName(name)} ${formatSimilarity(value.avg)}`
      )
      .join(", ");
    return {
      label:
        aggregate.avg != null
          ? `avg ${formatSimilarity(aggregate.avg)}`
          : `${formatCount(aggregate.count)} scored`,
      title:
        [
          aggregate.count != null ? `Scored candidates: ${formatCount(aggregate.count)}` : null,
          aggregate.min != null ? `Min: ${formatSimilarity(aggregate.min)}` : null,
          aggregate.max != null ? `Max: ${formatSimilarity(aggregate.max)}` : null,
          componentTitle ? `Weakest components: ${componentTitle}` : null,
        ]
          .filter((value): value is string => Boolean(value))
          .join(" | ") || undefined,
    };
  }

  const highFidelityCount = qualities.filter(
    (quality) => quality.high_fidelity
  ).length;
  const avgScore =
    qualities.reduce((sum, quality) => sum + (quality.score as number), 0) /
    qualities.length;
  const componentTotals = new Map<string, { sum: number; count: number }>();
  qualities.forEach((quality) => {
    Object.entries(quality.components || {}).forEach(([name, value]) => {
      if (!Number.isFinite(value)) return;
      const current = componentTotals.get(name) || { sum: 0, count: 0 };
      componentTotals.set(name, {
        sum: current.sum + value,
        count: current.count + 1,
      });
    });
  });
  const componentTitle = Array.from(componentTotals.entries())
    .map(([name, value]) => ({
      name,
      avg: value.count ? value.sum / value.count : 0,
    }))
    .sort((a, b) => a.avg - b.avg || a.name.localeCompare(b.name))
    .slice(0, 4)
    .map(({ name, avg }) => `${formatFieldName(name)} ${formatSimilarity(avg)}`)
    .join(", ");
  const structureGateIssues = qualities.flatMap((quality) => {
    const gate = quality.structure_gate;
    if (!gate || gate.passed) return [];
    const failed = Object.entries(gate.failed_components || {}).map(
      ([component, detail]) =>
        `${formatFieldName(component)} ${formatSimilarity(detail.value)} < ${formatSimilarity(detail.threshold)}`
    );
    const missing = (gate.missing_components || []).map(
      (component) => `${formatFieldName(component)} missing`
    );
    return [...failed, ...missing];
  });
  const gateTitle = Array.from(new Set(structureGateIssues)).slice(0, 4).join(", ");

  return {
    label: `${formatCount(highFidelityCount)} / ${formatCount(qualities.length)} high`,
    title:
      [
        `Average fidelity: ${formatSimilarity(avgScore)}`,
        componentTitle ? `Weakest components: ${componentTitle}` : null,
        gateTitle ? `Structure gate issues: ${gateTitle}` : null,
      ]
        .filter((value): value is string => Boolean(value))
        .join(" | ") || undefined,
  };
};

export const summarizeSourceLineage = (
  synthesis: TrainingSynthesisSummary
): {
  label: string;
  statusLabel: string;
  title?: string;
  warning: boolean;
} | null => {
  const lineage =
    synthesis.accepted_source_lineage ??
    synthesis.quality_report?.summary?.accepted_source_lineage;
  if (!lineage) return null;

  const schemaVersion = lineage.schema_version ?? null;
  const supportedSchema = schemaVersion === "accepted-source-lineage-v1";
  const observed = lineage.observed_candidate_count ?? null;
  const hasExpected = lineage.expected_candidate_count != null;
  const expected = lineage.expected_candidate_count ?? null;
  const candidateCount = lineage.candidate_count ?? null;
  const baseCoverage = lineage.with_base_receipt_count ?? null;
  const acceptedCandidateCount =
    synthesis.quality_report?.summary?.accepted_count ??
    synthesis.bundle_candidates_accepted ??
    synthesis.synthetic_candidates_accepted ??
    null;
  const sourceCount = lineage.source_receipt_key_count ?? 0;
  const coverageStatus = (lineage.coverage_status || "").toLowerCase();
  const isCompleteStatus =
    coverageStatus === "complete" || coverageStatus === "full";
  const isCoverageSampled = coverageStatus === "sampled";
  const unverifiedCoverageStatus = !isCompleteStatus;
  const nonAuthoritative = lineage.authoritative === false;
  const countMismatch =
    observed != null && expected != null && observed !== expected;
  const missingObservedCoverage =
    observed == null && (expected != null || candidateCount != null);
  const hasAcceptedCandidateTarget =
    acceptedCandidateCount != null && acceptedCandidateCount > 0;
  const baseCoverageUnavailable =
    supportedSchema &&
    isCompleteStatus &&
    !nonAuthoritative &&
    hasAcceptedCandidateTarget &&
    baseCoverage == null;
  const baseCoverageMismatch =
    supportedSchema &&
    isCompleteStatus &&
    !nonAuthoritative &&
    hasAcceptedCandidateTarget &&
    baseCoverage != null &&
    baseCoverage !== acceptedCandidateCount;
  const hasCompleteBaseCoverage =
    supportedSchema &&
    isCompleteStatus &&
    !nonAuthoritative &&
    hasAcceptedCandidateTarget &&
    baseCoverage != null &&
    baseCoverage === acceptedCandidateCount;
  // Candidate coverage counts can be bounded diagnostics; exact accepted
  // base-link coverage is the training gate surfaced by this badge.
  const blockingBaseCoverage =
    baseCoverageUnavailable || baseCoverageMismatch;
  const blockingCountMismatch = countMismatch && !hasCompleteBaseCoverage;
  const blockingMissingObservedCoverage =
    missingObservedCoverage && !hasCompleteBaseCoverage;
  const statusPrefix = isCoverageSampled
    ? "sampled"
    : nonAuthoritative
      ? "not auth"
      : blockingCountMismatch
        ? "gap"
        : blockingBaseCoverage
          ? "base"
          : unverifiedCoverageStatus
            ? "review"
            : null;
  const warning =
    !supportedSchema ||
    nonAuthoritative ||
    isCoverageSampled ||
    unverifiedCoverageStatus ||
    blockingCountMismatch ||
    blockingMissingObservedCoverage ||
    blockingBaseCoverage ||
    Boolean(lineage.coverage_warning) ||
    Boolean(lineage.source_receipt_keys_truncated);

  let label = "reported";
  if (!supportedSchema) {
    label = "schema review";
  } else if (
    statusPrefix === "base" &&
    baseCoverage != null &&
    acceptedCandidateCount != null
  ) {
    label = `base ${formatCount(baseCoverage)} / ${formatCount(
      acceptedCandidateCount
    )}`;
  } else if (statusPrefix === "base" && acceptedCandidateCount != null) {
    label = `base expected ${formatCount(acceptedCandidateCount)}`;
  } else if (statusPrefix && observed != null && expected != null) {
    label = `${statusPrefix} ${formatCount(observed)} / ${formatCount(expected)}`;
  } else if (statusPrefix && observed != null) {
    label = `${statusPrefix} ${formatCount(observed)}`;
  } else if (statusPrefix && expected != null) {
    label = `${statusPrefix} expected ${formatCount(expected)}`;
  } else if (statusPrefix && candidateCount != null) {
    label = `${statusPrefix} expected ${formatCount(candidateCount)}`;
  } else if (hasCompleteBaseCoverage && baseCoverage != null) {
    label = `${formatCount(baseCoverage)} base-linked`;
  } else if (observed != null) {
    label = `${formatCount(observed)} candidate${observed === 1 ? "" : "s"}`;
  } else if (candidateCount != null) {
    label = `expected ${formatCount(candidateCount)}`;
  } else if (sourceCount) {
    label = `${formatCount(sourceCount)} source receipts`;
  } else if (warning) {
    label = "review";
  }

  let statusLabel = "Lineage";
  if (nonAuthoritative) {
    statusLabel = "Lineage (not auth)";
  } else if (isCoverageSampled) {
    statusLabel = "Lineage (sampled)";
  } else if (!supportedSchema) {
    statusLabel = "Lineage (schema)";
  } else if (blockingCountMismatch || blockingMissingObservedCoverage) {
    statusLabel = "Lineage (gap)";
  } else if (blockingBaseCoverage) {
    statusLabel = "Lineage (base)";
  } else if (unverifiedCoverageStatus) {
    statusLabel = "Lineage (review)";
  }

  const flagTitle = (
    name: keyof Pick<
      TrainingSynthesisAcceptedSourceLineage,
      | "with_base_receipt_count"
      | "with_cross_receipt_item_count"
      | "with_category_evidence_count"
      | "with_nearest_real_structure_count"
      | "with_layout_integrity_count"
      | "with_arithmetic_reconciliation_count"
      | "with_selection_evidence_count"
    >,
    label: string
  ) =>
    lineage[name] != null
      ? `${label}: ${formatCount(lineage[name] as number)}`
      : null;

  let statusTitle = "Source lineage covers accepted candidates";
  if (!supportedSchema) {
    statusTitle = `Source lineage schema is ${
      schemaVersion ? `unsupported (${schemaVersion})` : "missing"
    }`;
  } else if (isCoverageSampled && nonAuthoritative) {
    statusTitle = "Source lineage is sampled and not authoritative";
  } else if (isCoverageSampled) {
    statusTitle = "Source lineage is sampled";
  } else if (nonAuthoritative) {
    statusTitle = "Source lineage is not authoritative";
  } else if (blockingCountMismatch) {
    statusTitle = "Source lineage count does not match expected coverage";
  } else if (blockingMissingObservedCoverage) {
    statusTitle = "Source lineage observed coverage is unavailable";
  } else if (baseCoverageUnavailable) {
    statusTitle = "Source lineage base evidence is unavailable";
  } else if (baseCoverageMismatch) {
    statusTitle = "Source lineage base evidence does not match accepted coverage";
  } else if (unverifiedCoverageStatus) {
    statusTitle = "Source lineage coverage status needs review";
  }

  const title = [
    statusTitle,
    observed != null && hasExpected && expected != null
      ? `Coverage: ${formatCount(observed)} / ${formatCount(expected)}`
      : null,
    observed == null && hasExpected && expected != null
      ? `Observed candidates: unavailable / ${formatCount(expected)} expected`
      : null,
    acceptedCandidateCount != null
      ? `Accepted candidates: ${formatCount(acceptedCandidateCount)}`
      : null,
    candidateCount != null ? `Candidate count: ${formatCount(candidateCount)}` : null,
    sourceCount
      ? `Source receipts: ${formatCount(sourceCount)}${
          lineage.source_receipt_keys_redacted ? " (IDs redacted)" : ""
        }`
      : null,
    lineage.source_receipt_keys_truncated
      ? "Source receipt list was truncated"
      : null,
    lineage.coverage_warning
      ? `Warning: ${formatSyntheticRejectionReason(lineage.coverage_warning)}`
      : null,
    flagTitle("with_base_receipt_count", "Base receipt evidence"),
    flagTitle("with_cross_receipt_item_count", "Cross-receipt item evidence"),
    flagTitle("with_category_evidence_count", "Category evidence"),
    flagTitle("with_nearest_real_structure_count", "Nearest-real structure"),
    flagTitle("with_layout_integrity_count", "Layout evidence"),
    flagTitle("with_arithmetic_reconciliation_count", "Arithmetic evidence"),
    flagTitle("with_selection_evidence_count", "Selection evidence"),
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return {
    label,
    statusLabel,
    title: title || undefined,
    warning,
  };
};

export const summarizeMixBalance = (
  balance?: TrainingSynthesisMixBalance
): { label: string; title?: string } | null => {
  if (!balance) return null;
  const risk = (balance.risk_level || "").toLowerCase();
  const label =
    risk && risk !== "none"
      ? `${formatCategoryName(risk)} risk`
      : balance.merchant_count != null && balance.operation_count != null
        ? `${formatCount(balance.merchant_count)} merchants · ${formatCount(balance.operation_count)} ops`
        : null;
  if (!label) return null;

  const title = [
    balance.top_merchant
      ? `Top merchant: ${balance.top_merchant}${
          balance.top_merchant_share != null
            ? ` (${formatPercent(balance.top_merchant_share)})`
            : ""
        }`
      : null,
    balance.top_operation
      ? `Top operation: ${formatOperationName(balance.top_operation)}${
          balance.top_operation_share != null
            ? ` (${formatPercent(balance.top_operation_share)})`
            : ""
        }`
      : null,
    balance.merchant_entropy != null
      ? `Merchant entropy: ${formatSimilarity(balance.merchant_entropy)}`
      : null,
    balance.operation_entropy != null
      ? `Operation entropy: ${formatSimilarity(balance.operation_entropy)}`
      : null,
    (balance.risk_reasons || []).length
      ? `Reasons: ${(balance.risk_reasons || [])
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return { label, title: title || undefined };
};

export const summarizeTrainingBatchPolicy = (
  policy?: TrainingSynthesisTrainingBatchPolicy
): { label: string; title?: string; warning?: boolean } | null => {
  if (!policy || !policy.status) return null;
  const status = policy.status.toLowerCase();
  const recommended = policy.recommended_example_count ?? 0;
  const label =
    status === "hold"
      ? "hold"
      : status === "smoke_test_only"
        ? `smoke ${formatCount(recommended)}`
        : status === "bounded_augmentation"
          ? `train ${formatCount(recommended)}`
          : formatCategoryName(policy.status);
  const title = [
    `Recommended examples: ${formatCount(recommended)}`,
    policy.accepted_candidate_count != null
      ? `Accepted candidates: ${formatCount(policy.accepted_candidate_count)}`
      : null,
    policy.selected_candidate_count != null
      ? `Selected rows: ${formatCount(policy.selected_candidate_count)}`
      : null,
    policy.high_fidelity_candidate_count != null
      ? `High fidelity: ${formatCount(policy.high_fidelity_candidate_count)}`
      : null,
    policy.max_synthetic_train_share != null
      ? `Max synthetic train share: ${formatPercent(policy.max_synthetic_train_share)}`
      : null,
    policy.overtraining_risk_level
      ? `Overtraining risk: ${formatCategoryName(policy.overtraining_risk_level)}`
      : null,
    (policy.risk_reasons || []).length
      ? `Risk reasons: ${(policy.risk_reasons || [])
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null,
    (policy.hold_reasons || []).length
      ? `Hold reasons: ${(policy.hold_reasons || [])
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null,
    policy.max_per_merchant != null && policy.max_per_merchant_operation != null
      ? `Caps: ${formatCount(policy.max_per_merchant)} / merchant, ${formatCount(policy.max_per_merchant_operation)} / merchant-operation`
      : null,
    policy.requires_real_validation_split ? "Validation: real receipts only" : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return {
    label,
    title: title || undefined,
    warning: policy.review_required === true || status !== "bounded_augmentation",
  };
};

export const summarizeRealBaselineComparison = (
  baseline?: TrainingSynthesisRealBaselineSummary | null
): { label: string; title?: string } | null => {
  if (!baseline || !baseline.count) return null;
  const within = baseline.within_real_score_range_count ?? 0;
  const label = `${formatCount(within)} / ${formatCount(
    baseline.count
  )} in range`;
  const title = [
    baseline.within_real_score_range_share != null
      ? `In-range share: ${formatPercent(baseline.within_real_score_range_share)}`
      : null,
    baseline.delta_from_avg?.avg != null
      ? `Avg delta vs real avg: ${baseline.delta_from_avg.avg >= 0 ? "+" : ""}${formatSimilarity(
          baseline.delta_from_avg.avg
        )}`
      : null,
    baseline.delta_from_min?.min != null
      ? `Worst delta vs real min: ${baseline.delta_from_min.min >= 0 ? "+" : ""}${formatSimilarity(
          baseline.delta_from_min.min
        )}`
      : null,
    baseline.baseline_pair_count?.avg != null
      ? `Avg real receipt pairs: ${formatSimilarity(baseline.baseline_pair_count.avg)}`
      : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return { label, title: title || undefined };
};

export const STRUCTURE_COMPONENT_LABELS: Record<string, string> = {
  price_column: "Price column",
  line_step: "Row spacing",
  category_sequence: "Category order",
  category_set: "Category match",
  token_count: "Token count",
};

export const summarizeStructureComponentGate = (
  synthesis: TrainingSynthesisSummary
): { label: string; title?: string } | null => {
  const thresholds =
    synthesis.quality_report?.quality_gates?.structure_component_thresholds;
  const acceptedComponents =
    synthesis.quality_report?.summary?.accepted_structure_components ??
    synthesis.accepted_structure_components;
  const entries = Object.entries(thresholds || {})
    .filter(([, value]) => Number.isFinite(value))
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!entries.length) return null;

  const [primaryName, primaryThreshold] = entries[0];
  const title = entries
    .map(([name, threshold]) => {
      const avg = acceptedComponents?.[name]?.avg;
      const accepted = Number.isFinite(avg)
        ? `, accepted avg ${formatSimilarity(avg)}`
        : "";
      return `${STRUCTURE_COMPONENT_LABELS[name] || formatFieldName(name)} >= ${formatPercent(threshold)}${accepted}`;
    })
    .join(", ");
  return {
    label: `${STRUCTURE_COMPONENT_LABELS[primaryName] || formatFieldName(primaryName)} ${formatPercent(primaryThreshold)}`,
    title,
  };
};

export const formatEvidenceCheck = (check: string): string =>
  check
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");

export type SynthesisQualityExample = NonNullable<
  TrainingSynthesisQualityMerchant["accepted_examples"]
>[number];

export const formatPlural = (count: number, singular: string, plural = `${singular}s`): string =>
  `${formatCount(count)} ${count === 1 ? singular : plural}`;

export const RECEIPT_EXCERPT_LINE_MAX_CHARS = 96;
export const RECEIPT_EXCERPT_LINE_LIMIT = 3;

export const clipReceiptExcerptLine = (value: string): string =>
  value.length <= RECEIPT_EXCERPT_LINE_MAX_CHARS
    ? value
    : `${value.slice(0, RECEIPT_EXCERPT_LINE_MAX_CHARS - 3)}...`;

export const summarizeExampleGrounding = (
  example?: SynthesisQualityExample
): string | null => {
  if (!example) return null;
  const catalog = example.catalog_grounding;
  const placement = example.category_placement;
  const removal = example.removal_context;
  const structure = example.structure_evidence;
  const parts: string[] = [];
  const outsideCount = catalog?.product_seen_outside_base_count;
  if (outsideCount && outsideCount > 0) {
    parts.push(formatPlural(outsideCount, "external receipt"));
  }

  const category = placement?.category || catalog?.category || example.category;
  const categoryCount =
    placement?.category_seen_count ?? catalog?.category_seen_count ?? null;
  if (category && categoryCount && categoryCount > 0) {
    parts.push(
      `${formatCategoryName(category)} in ${formatPlural(categoryCount, "receipt")}`
    );
  }

  const headingCount =
    placement?.category_heading_seen_count ??
    catalog?.category_heading_seen_count ??
    null;
  if (headingCount && headingCount > 0) {
    parts.push(formatPlural(headingCount, "heading"));
  }

  if (placement?.category_alignment === "same_category_as_base") {
    parts.push("same section");
  }

  if (
    removal?.category_item_count_before != null &&
    removal.category_item_count_before > 1 &&
    removal.category_item_count_after != null &&
    removal.category_item_count_after >= 1
  ) {
    const after = removal.category_item_count_after;
    const categoryLabel = removal.category
      ? formatCategoryName(removal.category)
      : "category";
    parts.push(
      `${categoryLabel} ${formatCount(removal.category_item_count_before)} -> ${formatCount(after)} items`
    );
  }

  if (removal?.shifted_line_count && removal.shifted_line_count > 0) {
    parts.push(formatPlural(removal.shifted_line_count, "shifted line"));
  }

  const shapeCheckCount = structure?.match_summary?.shape_checks?.length || 0;
  if (shapeCheckCount > 0) {
    parts.push(formatPlural(shapeCheckCount, "shape check"));
  } else if (structure?.nearest_real_receipt_key) {
    parts.push("nearest real shape");
  }

  return parts.slice(0, 2).join(" · ") || null;
};

export const summarizeStructureEvidence = (
  structure?: TrainingSynthesisStructureEvidence
): { label?: string; title?: string } => {
  if (!structure) return {};
  const shapeChecks = structure.match_summary?.shape_checks || [];
  const weakComponents = structure.match_summary?.weak_components || [];
  const baseline = structure.real_baseline_comparison;
  const baselineStatus =
    baseline?.within_real_score_range === true
      ? "within real range"
      : baseline?.within_real_score_range === false
        ? "below real range"
        : null;
  const similarityLabel =
    structure.score != null ? `${formatSimilarity(structure.score)} sim` : null;
  const label = shapeChecks.length
    ? [
        formatPlural(shapeChecks.length, "shape check"),
        similarityLabel,
        baselineStatus,
      ]
        .filter(Boolean)
        .join(" · ")
    : structure.nearest_real_receipt_key
      ? ["nearest real", similarityLabel, baselineStatus]
          .filter(Boolean)
          .join(" · ")
      : undefined;
  const delta = structure.shape_deltas || {};
  const baselineDelta =
    baseline?.delta_from_avg != null
      ? `, candidate ${baseline.delta_from_avg >= 0 ? "+" : ""}${formatSimilarity(
          baseline.delta_from_avg
        )} vs avg`
      : "";
  const title = [
    structure.nearest_real_receipt_key
      ? `Nearest real receipt: ${structure.nearest_real_receipt_key}`
      : null,
    baseline
      ? `Real baseline: avg ${formatSimilarity(
          baseline.baseline_avg
        )}, range ${formatSimilarity(baseline.baseline_min)}-${formatSimilarity(
          baseline.baseline_max
        )}${baselineDelta}`
      : null,
    shapeChecks.length
      ? `Shape checks: ${shapeChecks.map(formatEvidenceCheck).join(", ")}`
      : null,
    weakComponents.length
      ? `Weak components: ${weakComponents.map(formatEvidenceCheck).join(", ")}`
      : null,
    Object.keys(delta).length
      ? `Deltas: ${Object.entries(delta)
          .map(([key, value]) => `${formatEvidenceCheck(key)} ${value}`)
          .join(", ")}`
      : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return { label, title: title || undefined };
};

export const summarizeLayoutIntegrity = (
  layout?: TrainingSynthesisLayoutIntegrityEvidence
): { label?: string; title?: string } => {
  if (!layout) return {};
  const issueCount =
    (layout.overlap_pair_count ?? 0) +
    (layout.out_of_bounds_word_count ?? 0) +
    (layout.invalid_word_box_count ?? 0) +
    (layout.line_order_valid === false ? 1 : 0);
  const score =
    layout.score != null ? ` ${formatSimilarity(layout.score)}` : "";
  const label =
    layout.passed === true
      ? `layout ok${score}`
      : issueCount > 0
        ? `layout risk ${formatCount(issueCount)}`
        : `layout unchecked${score}`;
  const title = [
    layout.passed === true
      ? "Layout integrity passed"
      : layout.passed === false
        ? "Layout integrity did not pass"
        : "Layout integrity not reported",
    layout.score != null ? `score ${formatSimilarity(layout.score)}` : null,
    layout.overlap_pair_count != null
      ? `${formatCount(layout.overlap_pair_count)} overlap pairs`
      : null,
    layout.out_of_bounds_word_count != null
      ? `${formatCount(layout.out_of_bounds_word_count)} out-of-bounds words`
      : null,
    layout.invalid_word_box_count != null
      ? `${formatCount(layout.invalid_word_box_count)} invalid boxes`
      : null,
    layout.line_order_valid === false ? "line order invalid" : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");
  return { label, title: title || undefined };
};

export const summarizeSelectionEvidence = (
  example?: SynthesisQualityExample
): { label?: string; title?: string } => {
  const evidence = example?.selection_evidence;
  if (!evidence) return {};
  const selectedFrom = evidence.selected_from_candidate_count;
  const score = evidence.selected_score || {};
  const labelParts = [
    selectedFrom && selectedFrom > 1
      ? `selected from ${formatCount(selectedFrom)}`
      : null,
    Number.isFinite(score.candidate_quality)
      ? `quality ${formatSimilarity(score.candidate_quality)}`
      : null,
    score.within_real_score_range === true
      ? "real range"
      : score.within_real_score_range === false
        ? "below range"
        : null,
  ].filter((value): value is string => Boolean(value));
  const rankedBy = (evidence.ranked_by || [])
    .slice(0, 4)
    .map(formatEvidenceCheck);
  const title = [
    selectedFrom != null
      ? `Selected from ${formatCount(selectedFrom)} feasible mutation${
          selectedFrom === 1 ? "" : "s"
        }`
      : null,
    score.candidate_quality != null
      ? `Candidate quality: ${formatSimilarity(score.candidate_quality)}`
      : null,
    score.structure_similarity != null
      ? `Structure similarity: ${formatSimilarity(score.structure_similarity)}`
      : null,
    score.layout_integrity != null
      ? `Layout integrity: ${formatSimilarity(score.layout_integrity)}`
      : null,
    score.delta_from_min != null
      ? `Delta from real baseline min: ${
          score.delta_from_min >= 0 ? "+" : ""
        }${formatSimilarity(score.delta_from_min)}`
      : null,
    score.baseline_pair_count != null
      ? `Real baseline pairs: ${formatCount(score.baseline_pair_count)}`
      : null,
    rankedBy.length ? `Ranked by: ${rankedBy.join(", ")}` : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");
  return {
    label: labelParts.join(" · ") || undefined,
    title: title || undefined,
  };
};

export const summarizeReceiptShape = (
  example?: SynthesisQualityExample
): { label?: string; title?: string } => {
  if (!example) return {};
  const shape = example.receipt_shape || {};
  const previewLines = example.preview_lines || [];
  const focusedLine =
    previewLines.find((line) => line.synthetic_insert) ||
    previewLines.find((line) => (line.modified_labels || []).length > 0) ||
    previewLines[0];
  const lineCount = shape.line_count ?? null;
  const tokenCount = shape.token_count ?? null;
  const labelParts = [
    lineCount != null ? formatPlural(lineCount, "line") : null,
    tokenCount != null ? formatPlural(tokenCount, "token") : null,
  ].filter((value): value is string => Boolean(value));
  const bbox = focusedLine?.bbox;
  const bboxLabel =
    Array.isArray(bbox) && bbox.length === 4
      ? `[${bbox.map((value) => String(value)).join(" / ")}]`
      : null;
  const receiptExcerptLines = previewLines
    .map((line) => {
      const text = line.text?.replace(/\s+/g, " ").trim();
      if (!text) return null;
      const prefix = line.line_number != null ? `[${line.line_number}] ` : "";
      return clipReceiptExcerptLine(`${prefix}${text}`);
    })
    .filter((value): value is string => Boolean(value));
  const receiptExcerpt =
    receiptExcerptLines.length > 0
      ? `${receiptExcerptLines.slice(0, RECEIPT_EXCERPT_LINE_LIMIT).join(" ; ")}${
          receiptExcerptLines.length > RECEIPT_EXCERPT_LINE_LIMIT
            ? ` (+${receiptExcerptLines.length - RECEIPT_EXCERPT_LINE_LIMIT} more)`
            : ""
        }`
      : "";
  const title = [
    labelParts.length ? `Shape: ${labelParts.join(", ")}` : null,
    shape.truncated === true ? "Preview truncated" : null,
    focusedLine?.text ? `Focused line: ${focusedLine.text}` : null,
    focusedLine?.line_number != null
      ? `Line number: ${formatCount(focusedLine.line_number)}`
      : null,
    focusedLine?.y != null
      ? `Line y (normalized): ${formatSimilarity(focusedLine.y)}`
      : null,
    bboxLabel ? `Line bbox (receipt coords): ${bboxLabel}` : null,
    receiptExcerpt ? `Receipt excerpt: ${receiptExcerpt}` : null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return {
    label: labelParts.join(" · ") || undefined,
    title: title || undefined,
  };
};

export const summarizeReceiptPreview = (
  examples: TrainingSynthesisSummary["candidate_examples"] | undefined
): { label: string; title?: string } | null => {
  const example = (examples || []).find(
    (candidate) =>
      candidate.receipt_preview?.text ||
      candidate.accuracy_evidence?.changed_text ||
      candidate.item_text ||
      candidate.new_text
  );
  if (!example) return null;

  const previewLines = example.receipt_preview?.lines || [];
  const changedLine =
    previewLines.find((line) => line.synthetic_insert)?.text ||
    previewLines.find((line) => (line.modified_labels || []).length > 0)?.text ||
    example.accuracy_evidence?.changed_text ||
    example.item_text ||
    (example.field_label && example.new_text
      ? `${formatFieldName(example.field_label)} ${example.new_text}`
      : null);
  if (!changedLine) return null;

  const category =
    example.category || example.accuracy_evidence?.category || undefined;
  const detailParts = [
    example.merchant_name,
    example.operation ? formatOperationName(example.operation) : null,
    category ? formatCategoryName(category) : null,
    example.structure_similarity != null
      ? `similarity ${formatSimilarity(example.structure_similarity)}`
      : null,
  ].filter((value): value is string => Boolean(value));
  const checks = (example.accuracy_evidence?.checks || [])
    .slice(0, 4)
    .map(formatEvidenceCheck);
  const structureSummary = summarizeStructureEvidence(
    example.accuracy_evidence?.structure_similarity
  );
  const layoutSummary = summarizeLayoutIntegrity(
    example.accuracy_evidence?.layout_integrity
  );
  const selectionSummary = summarizeSelectionEvidence(
    candidateExampleToQualityExample(example)
  );
  const titleParts = [
    detailParts.join(" | "),
    example.receipt_preview?.text,
    layoutSummary.title,
    structureSummary.title,
    selectionSummary.title,
    checks.length ? `Checks: ${checks.join(", ")}` : null,
  ].filter((value): value is string => Boolean(value));

  return {
    label: changedLine,
    title: titleParts.join("\n"),
  };
};

export const summarizeOperationCounts = (
  counts: Record<string, number> | undefined,
  fallbackOperations: string[] | undefined
): string => {
  const entries = Object.entries(counts || {})
    .filter(([operation, count]) => operation && count > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (entries.length) {
    return entries
      .slice(0, 2)
      .map(
        ([operation, count]) =>
          `${formatOperationName(operation)} ${formatCount(count)}`
      )
      .join(" · ");
  }
  const operations = (fallbackOperations || []).filter(Boolean);
  if (operations.length) {
    return operations.slice(0, 2).map(formatOperationName).join(" · ");
  }
  return "No accepted mutations";
};

export const formatReadinessStatus = (status?: string | null): string =>
  status ? formatCategoryName(status) : "Unknown";

export const candidateExampleToQualityExample = (
  example: NonNullable<TrainingSynthesisSummary["candidate_examples"]>[number]
): NonNullable<TrainingSynthesisQualityMerchant["accepted_examples"]>[number] => ({
  candidate_id: example.candidate_id,
  operation: example.operation,
  category: example.category || example.accuracy_evidence?.category,
  changed_text:
    example.accuracy_evidence?.changed_text ||
    example.item_text ||
    example.new_text,
  label: example.field_label,
  structure_similarity: example.structure_similarity,
  structure_evidence: example.accuracy_evidence?.structure_similarity,
  candidate_quality: example.candidate_quality,
  selection_evidence: example.selection_evidence,
  layout_integrity: example.accuracy_evidence?.layout_integrity,
  accuracy_checks: example.accuracy_evidence?.checks || [],
  catalog_grounding: example.accuracy_evidence?.catalog_grounding,
  category_placement: example.accuracy_evidence?.category_placement,
  removal_context: example.accuracy_evidence?.removal_context,
  receipt_shape: {
    line_count: example.receipt_preview?.line_count,
    token_count: example.receipt_preview?.token_count,
    truncated: example.receipt_preview?.truncated,
  },
  preview_lines: example.receipt_preview?.lines || [],
});

export const merchantGapMap = (
  synthesis: TrainingSynthesisSummary
): Map<string, TrainingSynthesisMerchantGap> =>
  new Map(
    (synthesis.quality_report?.merchant_gap_summary?.merchants || [])
      .filter((merchant) => merchant.merchant_name)
      .map((merchant) => [merchant.merchant_name as string, merchant])
  );

export const mergeMerchantGapDetails = (
  merchant: TrainingSynthesisQualityMerchant,
  gap?: TrainingSynthesisMerchantGap
): TrainingSynthesisQualityMerchant => {
  if (!gap) return merchant;
  return {
    ...merchant,
    readiness_status: merchant.readiness_status ?? gap.status,
    readiness_score: merchant.readiness_score ?? gap.score,
    candidate_count: merchant.candidate_count ?? gap.candidate_count,
    accepted_count: merchant.accepted_count ?? gap.accepted_count,
    blockers: merchant.blockers?.length ? merchant.blockers : gap.blockers,
    limitations: merchant.limitations?.length
      ? merchant.limitations
      : gap.limitations,
    missing_operations: gap.missing_operations,
    operation_gap_reasons: gap.operation_gap_reasons,
  };
};

export const buildMerchantQualityRows = (
  synthesis: TrainingSynthesisSummary
): TrainingSynthesisQualityMerchant[] => {
  const gapsByMerchant = merchantGapMap(synthesis);
  const reportRows = synthesis.quality_report?.merchants || [];
  if (reportRows.length) {
    return reportRows
      .slice(0, 4)
      .map((merchant) =>
        mergeMerchantGapDetails(
          merchant,
          merchant.merchant_name
            ? gapsByMerchant.get(merchant.merchant_name)
            : undefined
        )
      );
  }

  const contractsByMerchant = new Map(
    (synthesis.merchant_synthesis_contracts || [])
      .filter((contract) => contract.merchant_name)
      .map((contract) => [contract.merchant_name as string, contract])
  );
  const examplesByMerchant = new Map<string, ReturnType<typeof candidateExampleToQualityExample>[]>();
  (synthesis.candidate_examples || []).forEach((example) => {
    const merchant = example.merchant_name || "Unknown merchant";
    const rows = examplesByMerchant.get(merchant) || [];
    rows.push(candidateExampleToQualityExample(example));
    examplesByMerchant.set(merchant, rows);
  });

  return (synthesis.candidate_mix_merchants || []).slice(0, 4).map((merchant) => {
    const merchantName = merchant.merchant_name || "Unknown merchant";
    const contract = contractsByMerchant.get(merchantName);
    const candidateCount = merchant.candidate_count ?? null;
    const acceptedCount = merchant.accepted_count ?? null;
    const acceptanceRate =
      candidateCount && acceptedCount != null ? acceptedCount / candidateCount : null;
    return mergeMerchantGapDetails({
      merchant_name: merchantName,
      readiness_status: contract?.status,
      readiness_score: contract?.score,
      source_receipt_count: contract?.source_receipt_count,
      candidate_count: candidateCount,
      accepted_count: acceptedCount,
      rejected_count: merchant.rejected_count,
      acceptance_rate: acceptanceRate,
      supported_operations: contract?.supported_operations,
      contract_ready_operations: contract?.ready_operations,
      accepted_operation_counts: merchant.accepted_operation_counts,
      accepted_category_counts: merchant.accepted_category_counts,
      accepted_field_replacement_counts: contract?.accepted_field_replacement_counts,
      accepted_structure_similarity: merchant.accepted_structure_similarity,
      rejection_reasons: merchant.rejection_reasons,
      blockers: contract?.blockers,
      limitations: contract?.limitations,
      accepted_examples: examplesByMerchant.get(merchantName)?.slice(0, 3) || [],
    }, gapsByMerchant.get(merchantName));
  });
};

export const summarizeMerchantOperationGaps = (
  merchant: TrainingSynthesisQualityMerchant
): { label: string; title?: string } | null => {
  const missingOperations = (merchant.missing_operations || []).filter(Boolean);
  const blockers = (merchant.blockers || []).filter(Boolean);
  const limitations = (merchant.limitations || []).filter(Boolean);
  const nextActions = (merchant.next_synthesis_actions || []).filter(Boolean);
  const operationReasonEntries = Object.entries(
    merchant.operation_gap_reasons || {}
  ).filter(([operation, reasons]) => operation && reasons.length);

  if (
    !missingOperations.length &&
    !blockers.length &&
    !limitations.length &&
    !nextActions.length &&
    !operationReasonEntries.length
  ) {
    return null;
  }

  const label = missingOperations.length
      ? `Needs ${missingOperations.slice(0, 2).map(formatOperationName).join(", ")}${
        missingOperations.length > 2 ? `, +${formatCount(missingOperations.length - 2)}` : ""
      }`
    : blockers.length
      ? `Blocked: ${formatSyntheticRejectionReason(blockers[0])}`
      : limitations.length
        ? `Limited: ${formatSyntheticRejectionReason(limitations[0])}`
        : nextActions.length
          ? `Next: ${formatSyntheticRejectionReason(nextActions[0])}`
          : `Gap: ${formatOperationName(operationReasonEntries[0][0])}`;

  const title = [
    missingOperations.length
      ? `Missing operations: ${missingOperations.map(formatOperationName).join(", ")}`
      : null,
    blockers.length
      ? `Blockers: ${blockers.map(formatSyntheticRejectionReason).join(", ")}`
      : null,
    limitations.length
      ? `Limitations: ${limitations.map(formatSyntheticRejectionReason).join(", ")}`
      : null,
    nextActions.length
      ? `Next actions: ${nextActions
          .slice(0, 4)
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
      : null,
    ...operationReasonEntries.map(
      ([operation, reasons]) =>
        `${formatOperationName(operation)}: ${reasons
          .map(formatSyntheticRejectionReason)
          .join(", ")}`
    ),
  ]
    .filter((value): value is string => Boolean(value))
    .join(" | ");

  return {
    label,
    title: title || undefined,
  };
};
