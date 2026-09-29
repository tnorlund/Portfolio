const EVIDENCE_TITLE =
  /^Evidence(?:[ \t]+(?:samples?|citations?))?(?:[ \t]+\([^()\r\n]*\))?:?$/i;
const CODE_FENCE = /^[ \t]{0,3}(`{3,}|~{3,})([^\r\n]*)$/;

const isCitationJson = (text: string): boolean => {
  try {
    const value: unknown = JSON.parse(text);
    const citations =
      value && typeof value === "object" && "evidence" in value
        ? value.evidence
        : value;
    return (
      Array.isArray(citations) &&
      citations.every(
        (row: unknown) =>
          row !== null &&
          typeof row === "object" &&
          "image_id" in row &&
          typeof row.image_id === "string",
      )
    );
  } catch {
    return false;
  }
};

/** Remove citation appendices without treating answer prose as a heading. */
export const stripEvidenceAppendix = (answer: string): string => {
  const lines = answer.split(/\r?\n/);
  let fence: { marker: string; start: number; json: boolean } | undefined;

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const marker = line.match(CODE_FENCE);

    if (fence) {
      if (
        marker &&
        marker[1][0] === fence.marker[0] &&
        marker[1].length >= fence.marker.length &&
        !marker[2].trim()
      ) {
        if (
          fence.json &&
          !lines.slice(index + 1).join("\n").trim() &&
          isCitationJson(lines.slice(fence.start + 1, index).join("\n"))
        ) {
          return lines.slice(0, fence.start).join("\n").trim();
        }
        fence = undefined;
      }
      continue;
    }

    if (marker) {
      fence = {
        marker: marker[1],
        start: index,
        json: /^(?:json)?$/i.test(marker[2].trim()),
      };
      continue;
    }

    const title = line
      .trim()
      .replace(/^#{1,6}[ \t]+/, "")
      .replace(/\*\*|__/g, "")
      .trim();
    if (EVIDENCE_TITLE.test(title)) {
      return lines.slice(0, index).join("\n").trim();
    }
  }

  return answer.trim();
};
