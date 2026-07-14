/**
 * Shared label palette + display names for receipt-token visualizations
 * (LayoutLMBatchVisualization, AugmentationShowcase). Extracted so figures can
 * share one visual language without bundling each other's components.
 */

export const CHARGE_GREEN = "var(--color-green)";
const CHARGE_GREEN_MED = "color-mix(in srgb, var(--color-green) 72%, white)";
const CHARGE_GREEN_LIGHT = "color-mix(in srgb, var(--color-green) 48%, white)";

// Label colors. The granular model splits currency into charges (green
// family) vs credits (teal), plus QUANTITY (cyan) and ADDRESS_LINE.
export const LABEL_COLORS: Record<string, string> = {
  MERCHANT_NAME: "var(--color-yellow)",
  BUSINESS_NAME: "var(--color-yellow)",
  LOYALTY_ID: "var(--color-yellow)",
  DATE: "var(--color-blue)",
  TIME: "var(--color-blue)",
  PRODUCT_NAME: "var(--color-purple)",
  // Charges — green family
  GRAND_TOTAL: CHARGE_GREEN,
  SUBTOTAL: CHARGE_GREEN_MED,
  TAX: CHARGE_GREEN_MED,
  LINE_TOTAL: CHARGE_GREEN_MED,
  UNIT_PRICE: CHARGE_GREEN_LIGHT,
  AMOUNT: CHARGE_GREEN, // legacy merged label (back-compat)
  // Credits / money back — teal
  DISCOUNT: "var(--color-teal)",
  COUPON: "var(--color-teal)",
  TIP: "var(--color-teal)",
  CHANGE: "var(--color-teal)",
  CASH_BACK: "var(--color-teal)",
  REFUND: "var(--color-teal)",
  // Quantity (a count, not a dollar amount)
  QUANTITY: "var(--color-cyan)",
  // Address / contact
  ADDRESS_LINE: "var(--color-red)",
  ADDRESS: "var(--color-red)", // back-compat
  PHONE_NUMBER: "var(--color-pink)",
  WEBSITE: "var(--color-purple)",
  STORE_HOURS: "var(--color-orange)",
  PAYMENT_METHOD: "var(--color-orange)",
  O: "var(--text-color)",
};

// Entity type display names
export const ENTITY_DISPLAY_NAMES: Record<string, string> = {
  MERCHANT_NAME: "Merchant",
  BUSINESS_NAME: "Business name",
  LOYALTY_ID: "Loyalty ID",
  DATE: "Date",
  TIME: "Time",
  PRODUCT_NAME: "Product",
  GRAND_TOTAL: "Total",
  SUBTOTAL: "Subtotal",
  TAX: "Tax",
  LINE_TOTAL: "Line total",
  UNIT_PRICE: "Unit price",
  AMOUNT: "Amount",
  DISCOUNT: "Discount",
  COUPON: "Coupon",
  TIP: "Tip",
  CHANGE: "Change",
  CASH_BACK: "Cash back",
  REFUND: "Refund",
  QUANTITY: "Qty",
  ADDRESS_LINE: "Address",
  ADDRESS: "Address",
  PHONE_NUMBER: "Phone",
  WEBSITE: "Website",
  STORE_HOURS: "Hours",
  PAYMENT_METHOD: "Payment",
};
