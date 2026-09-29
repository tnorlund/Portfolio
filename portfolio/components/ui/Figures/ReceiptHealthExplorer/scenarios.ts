import { api } from "../../../../services/api";
import type {
  ReceiptHealthCheck,
  ReceiptHealthLedgerIssue,
  ReceiptHealthReceipt,
} from "../../../../types/api";

type CheckId = ReceiptHealthCheck["id"];
type IssueQuery = Parameters<typeof api.fetchReceiptHealthIssues>[0];

export const CHECK_ORDER: CheckId[] = [
  "merchant_identity",
  "receipt_format",
  "financial_math",
];

interface HealthScenario {
  id: "clean" | "merchant" | "format" | "math" | "ocr" | "consistent";
  label: string;
  focus: CheckId;
  issueQuery?: IssueQuery;
}

const SCENARIOS: HealthScenario[] = [
  { id: "clean", label: "Clean", focus: "merchant_identity" },
  {
    id: "merchant", label: "Merchant", focus: "merchant_identity",
    issueQuery: { checkId: "merchant_identity" },
  },
  {
    id: "format", label: "Format", focus: "receipt_format",
    issueQuery: { checkId: "receipt_format" },
  },
  {
    id: "math", label: "Math", focus: "financial_math",
    issueQuery: { checkId: "financial_math" },
  },
  {
    id: "ocr", label: "OCR gap", focus: "financial_math",
    issueQuery: { checkId: "financial_math", classification: "reocr_needed" },
  },
  {
    id: "consistent", label: "Consistent", focus: "financial_math",
    issueQuery: { checkId: "financial_math", rootCause: "already_consistent_labels" },
  },
];

const BATCH_SIZE = 12;
const INITIAL_SEED = 29;
const CANDIDATE_LIMIT = 3;
const ISSUE_LIMIT = 12;
// Optional discovery must not keep a usable general batch behind the loader.
const REQUEST_TIMEOUT_MS = 3000;
const LOAD_TIMEOUT_MS = 8000;

export interface ReceiptHealthExample extends ReceiptHealthReceipt {
  scenario: HealthScenario | null;
}

function requestWithinBudget<T>(
  request: () => Promise<T>,
  deadline: number,
): Promise<T> {
  const remaining = Math.min(REQUEST_TIMEOUT_MS, deadline - Date.now());
  const timeoutError = new Error("Receipt health data request timed out");
  if (remaining <= 0) return Promise.reject(timeoutError);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(timeoutError), remaining);
    Promise.resolve().then(request).then(
      (result) => { clearTimeout(timer); resolve(result); },
      (error) => { clearTimeout(timer); reject(error); },
    );
  });
}

function receiptKey(receipt: { image_id: string; receipt_id: number }): string {
  return `${receipt.image_id}-${receipt.receipt_id}`;
}

function isIssueForScenario(
  issue: ReceiptHealthLedgerIssue,
  scenario: HealthScenario,
): boolean {
  if (issue.state === "resolved" || issue.check_id !== scenario.focus) return false;
  if (scenario.id === "ocr") {
    return issue.preflight?.classification === "reocr_needed";
  }
  if (scenario.id === "consistent") {
    return issue.preflight?.root_cause === "already_consistent_labels";
  }
  return issue.status === "fail" || issue.status === "review";
}

function matchesScenario(
  receipt: ReceiptHealthReceipt,
  scenario: HealthScenario,
  issues: ReceiptHealthLedgerIssue[],
): boolean {
  if (scenario.id === "clean") {
    return receipt.overall_status === "pass" &&
      receipt.summary.issue_count === 0 &&
      CHECK_ORDER.every((id) => receipt.checks.some(
        (check) => check.id === id && check.status === "pass",
      ));
  }
  const check = receipt.checks.find((check) => check.id === scenario.focus);
  if (check?.status !== "fail" && check?.status !== "review") return false;
  if (scenario.id === "ocr" || scenario.id === "consistent") {
    return issues.some((issue) =>
      receiptKey(issue) === receiptKey(receipt) &&
      issue.status === check.status &&
      isIssueForScenario(issue, scenario),
    );
  }
  return true;
}

export function selectReceiptHealthExamples(
  receipts: ReceiptHealthReceipt[],
  issues: ReceiptHealthLedgerIssue[],
): ReceiptHealthExample[] {
  const selected = new Map<HealthScenario["id"], ReceiptHealthExample>();
  const used = new Set<string>();
  // Reserve rare preflight examples before selecting a generic math failure.
  const selectionOrder = ["ocr", "consistent", "merchant", "format", "math", "clean"];
  for (const id of selectionOrder) {
    const scenario = SCENARIOS.find((candidate) => candidate.id === id)!;
    const receipt = receipts.find((candidate) =>
      !used.has(receiptKey(candidate)) && matchesScenario(candidate, scenario, issues),
    );
    if (receipt) {
      used.add(receiptKey(receipt));
      selected.set(scenario.id, { ...receipt, scenario });
    }
  }
  const examples = SCENARIOS.flatMap((scenario) => {
    const example = selected.get(scenario.id);
    return example ? [example] : [];
  });
  // Keep real cached receipts available even when a category cannot be verified.
  // Unclassified fallbacks never acquire a scenario label they cannot support.
  for (const receipt of receipts) {
    if (examples.length >= SCENARIOS.length) break;
    if (receipt.checks.length === 0 || used.has(receiptKey(receipt))) continue;
    used.add(receiptKey(receipt));
    examples.push({ ...receipt, scenario: null });
  }
  return examples;
}

export function preferredCheckIdForReceipt(
  receipt: ReceiptHealthExample | null | undefined,
): CheckId | null {
  const focus = receipt?.scenario?.focus;
  if (focus && receipt.checks.some((check) => check.id === focus)) return focus;
  return CHECK_ORDER.find((id) => receipt?.checks.some((check) =>
    check.id === id && (check.status === "fail" || check.status === "review"),
  )) ?? CHECK_ORDER.find((id) => receipt?.checks.some((check) => check.id === id)) ?? null;
}

export async function loadReceiptHealthExamples(): Promise<ReceiptHealthExample[]> {
  const deadline = Date.now() + LOAD_TIMEOUT_MS;
  const issueScenarios = SCENARIOS.filter((scenario) => scenario.issueQuery);
  const [batch, ...issueResults] = await Promise.allSettled([
    requestWithinBudget(
      () => api.fetchReceiptHealth(BATCH_SIZE, INITIAL_SEED, 0), deadline,
    ),
    ...issueScenarios.map((scenario) => requestWithinBudget(
      () => api.fetchReceiptHealthIssues({
        state: "all",
        ...scenario.issueQuery,
        limit: ISSUE_LIMIT,
      }), deadline,
    )),
  ]);
  const receipts = new Map<string, ReceiptHealthReceipt>();
  if (batch.status === "fulfilled" && "receipts" in batch.value) {
    for (const receipt of batch.value.receipts) receipts.set(receiptKey(receipt), receipt);
  }
  const issues: ReceiptHealthLedgerIssue[] = [];
  const candidates = issueScenarios.map((scenario, index) => {
    const result = issueResults[index];
    const candidates = result.status === "fulfilled" && "issues" in result.value
      ? result.value.issues.filter((issue) => isIssueForScenario(issue, scenario))
      : [];
    issues.push(...candidates);
    return { scenario, candidates: [...new Map(
      candidates.map((issue) => [receiptKey(issue), issue]),
    ).values()].slice(0, CANDIDATE_LIMIT) };
  });
  const requests = new Map<string, Promise<ReceiptHealthReceipt[]>>();
  let examples = selectReceiptHealthExamples([...receipts.values()], issues);
  for (let attempt = 0; attempt < CANDIDATE_LIMIT; attempt += 1) {
    const missing = candidates.filter(({ scenario, candidates }) =>
      candidates[attempt] && !examples.some((example) => example.scenario?.id === scenario.id),
    );
    if (missing.length === 0) break;
    const resolved = await Promise.all(missing.map(async ({ scenario, candidates }) => {
      const candidate = candidates[attempt];
      let request = requests.get(candidate.image_id);
      if (!request) {
        request = requestWithinBudget(
          () => api.fetchReceiptHealth(BATCH_SIZE, INITIAL_SEED, 0, {
            imageId: candidate.image_id,
          }), deadline,
        ).then((response) => response.receipts).catch(() => []);
        requests.set(candidate.image_id, request);
      }
      // The ledger can outlive a receipt or its evaluation. Require the exact
      // cached receipt and the current check outcome before using its example.
      const receipt = (await request).find((receipt) =>
        receiptKey(receipt) === receiptKey(candidate) &&
        matchesScenario(receipt, scenario, issues),
      );
      return receipt ?? null;
    }));
    for (const receipt of resolved) {
      if (receipt) receipts.set(receiptKey(receipt), receipt);
    }
    examples = selectReceiptHealthExamples([...receipts.values()], issues);
  }
  if (examples.length === 0 && batch.status === "rejected") throw batch.reason;
  return examples;
}
