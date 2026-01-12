/**
 * Format label/status for display: Title Case with special cases
 * - "id" becomes "ID"
 *
 * @example
 * formatLabel("LOYALTY_ID") // "Loyalty ID"
 * formatLabel("ADDRESS_LINE") // "Address Line"
 * formatLabel("NEEDS_REVIEW") // "Needs Review"
 */
export function formatLabel(text: string): string {
  return text
    .toLowerCase()
    .replace(/_/g, " ")
    .split(" ")
    .map((word) =>
      word === "id" ? "ID" : word.charAt(0).toUpperCase() + word.slice(1)
    )
    .join(" ");
}
