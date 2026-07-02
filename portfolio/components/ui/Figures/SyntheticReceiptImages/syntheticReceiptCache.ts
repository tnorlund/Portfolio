export interface SyntheticReceiptImage {
  id: string;
  title: string;
  operation: string;
  imageSrc: string;
  width: number;
  height: number;
  candidateId: string;
  source: string;
  baseReceiptKey: string;
  trainOnlyReason: string;
  tokenCount: number;
  lineCount: number;
  structureScore?: number;
  oldGrandTotal?: string;
  newGrandTotal?: string;
  labelTarget?: string;
  expectedEffect?: string;
}

export const syntheticReceiptImages: SyntheticReceiptImage[] = [
  {
    id: "sprouts-add-line-item",
    title: "Add line item",
    operation: "add_line_item",
    imageSrc: "/synthetic-receipts/sprouts_arithmetic_add_item.png",
    width: 576,
    height: 1176,
    candidateId: "sprouts-arithmetic-1-add-line-item-37333eb8-0025-4711-98ae-7b72fd83abd5-00001",
    source: "sprouts_arithmetic_geometry",
    baseReceiptKey: "37333eb8-0025-4711-98ae-7b72fd83abd5#00001",
    trainOnlyReason: "Arithmetic synthetic examples must not enter validation.",
    tokenCount: 203,
    lineCount: 83,
    structureScore: 0.948,
    oldGrandTotal: "29.08",
    newGrandTotal: "30.58",
    expectedEffect: "Adds an observed non-taxable produce item and reconciles the payment total.",
  },
  {
    id: "sprouts-remove-line-item",
    title: "Remove line item",
    operation: "remove_line_item",
    imageSrc: "/synthetic-receipts/sprouts_arithmetic_remove_item.png",
    width: 576,
    height: 1176,
    candidateId: "sprouts-arithmetic-2-remove-line-item-232ae902-fee0-426e-b1ea-488a806028ae-00001",
    source: "sprouts_arithmetic_geometry",
    baseReceiptKey: "232ae902-fee0-426e-b1ea-488a806028ae#00001",
    trainOnlyReason: "Arithmetic synthetic examples must not enter validation.",
    tokenCount: 172,
    lineCount: 69,
    structureScore: 0.834,
    oldGrandTotal: "4.49",
    newGrandTotal: "3.49",
    expectedEffect: "Removes a real observed produce item and balances the grand total.",
  },
  {
    id: "sprouts-address-hard-negative",
    title: "Address hard negative",
    operation: "hard_negative",
    imageSrc: "/synthetic-receipts/sprouts_synthetic_address_hard_negative.png",
    width: 560,
    height: 1280,
    candidateId: "sprouts-1-address-line-00ded398-af6f-4a49-86f7-c79ccb554e48-00002",
    source: "sprouts_parameterized_geometry",
    baseReceiptKey: "00ded398-af6f-4a49-86f7-c79ccb554e48#00002",
    trainOnlyReason: "Synthetic examples target known model confusions and must not enter validation.",
    tokenCount: 183,
    lineCount: 52,
    labelTarget: "ADDRESS_LINE false positive",
    expectedEffect: "Improve ADDRESS_LINE precision with one high-fidelity O hard negative.",
  },
];
