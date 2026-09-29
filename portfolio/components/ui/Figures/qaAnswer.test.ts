import { stripEvidenceAppendix } from "./qaAnswer";

describe("stripEvidenceAppendix", () => {
  it("preserves an Evidence table column, all rows, and inline evidence prose", () => {
    const body = [
      "**Receipts with items over $50**",
      "",
      "| Merchant | Item | Price | Evidence |",
      "| --- | --- | ---: | --- |",
      "| Harbor Market | Kitchen set | $75.00 | Receipt A |",
      "| Bicycle Shop | Helmet | $65.00 | Receipt B |",
      "",
      "The evidence supports both purchases over $50.",
    ].join("\n");
    expect(stripEvidenceAppendix(`${body}\n\n**Evidence**\n\n\`\`\`json\n[]\n\`\`\``))
      .toBe(body);
    expect(stripEvidenceAppendix(body)).toBe(body);
  });

  it.each([
    "Evidence:", "**Evidence:**", "### Evidence", "## **Evidence**",
    "**Evidence** (sample receipts):", "**Evidence samples:**",
  ])("removes the standalone appendix heading %s", (heading) => {
    expect(stripEvidenceAppendix(`The answer.\n\n${heading}\nCitation details.`))
      .toBe("The answer.");
  });

  it.each([
    '[{"image_id":"example-receipt","receipt_id":1,"amount":75}]',
    '{"total":75,"evidence":[{"image_id":"example-receipt","receipt_id":1}]}',
    "[]",
  ])("removes a trailing fenced citation payload: %s", (payload) => {
    expect(stripEvidenceAppendix(`The answer.\n\n\`\`\`json\n${payload}\n\`\`\``))
      .toBe("The answer.");
  });

  it.each([
    "Evidence is incomplete, so the total is unknown.",
    "## Evidence quality\n\nSome receipts have no recorded tip.",
    "```text\nEvidence\nThis is a quoted example.\n```",
    '```json\n[{"month":"January","total":75}]\n```',
    '```json\n[{"image_id":"example"}]\n```\n\nMore answer prose.',
    '```json\n[{"image_id":"example"}]',
  ])("preserves ordinary prose and non-appendix code: %s", (answer) => {
    expect(stripEvidenceAppendix(answer)).toBe(answer);
  });
});
