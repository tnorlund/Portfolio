import { useState, useEffect } from "react";
import { fetchWordTagList, fetchReceiptWordTags } from "./api";
import { ReceiptWordTagsApiResponse } from "./interfaces"; // adjust import if needed

// Helper function to format the timestamp
function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleString();
}

/**
 * Returns one of these symbols for the entire receipt:
 *   "✗" if ANY word is validated === false
 *   "✓" if ALL words are validated === true
 *   "?" otherwise (if any are null or it's a mix)
 */
function getOverallValidationStatus(
  items: { tag: { validated: boolean | null } }[]
): string {
  // An array of just the validated flags
  const flags = items.map((item) => item.tag.validated);

  // 1) If any are explicitly false => "✗"
  if (flags.includes(false)) {
    return "✗";
  }

  // 2) If *every* item is true => "✓"
  if (flags.every((f) => f === true)) {
    return "✓";
  }

  // 3) Otherwise => "?"
  return "?";
}

function ReceiptValidation() {
  const [tags, setTags] = useState<string[]>([]);
  const [selectedTag, setSelectedTag] = useState<string>("");
  const [loadingTags, setLoadingTags] = useState<boolean>(true);

  const [receiptTags, setReceiptTags] = useState<ReceiptWordTagsApiResponse | null>(null);
  const [loadingReceiptTags, setLoadingReceiptTags] = useState<boolean>(false);

  // Track expansion at the (imageId, receiptId) level
  const [expandedReceipts, setExpandedReceipts] = useState<{
    [imageId: string]: { [receiptId: string]: boolean };
  }>({});

  // Fetch the list of tags on mount
  useEffect(() => {
    fetchWordTagList()
      .then((data) => {
        setTags(data);
        if (data.length > 0) {
          setSelectedTag(data[0]);
        }
      })
      .catch((error) => {
        console.error("Failed to fetch word tags:", error);
      })
      .finally(() => {
        setLoadingTags(false);
      });
  }, []);

  // Whenever selectedTag changes, fetch the receipt word tags
  useEffect(() => {
    if (selectedTag) {
      setLoadingReceiptTags(true);
      fetchReceiptWordTags(selectedTag, 50)
        .then((data) => {
          setReceiptTags(data);
          // Reset expanded receipts when a new tag is selected
          setExpandedReceipts({});
        })
        .catch((error) => {
          console.error("Failed to fetch receipt word tags:", error);
        })
        .finally(() => {
          setLoadingReceiptTags(false);
        });
    }
  }, [selectedTag]);

  const handleTagChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedTag(event.target.value);
  };

  /**
   * Group items by image_id → receipt_id
   */
  const getGroupedData = () => {
    if (!receiptTags || !receiptTags.payload) return {};

    // Build a nested object: grouped[imageId][receiptId] = arrayOfItems
    const grouped = receiptTags.payload.reduce((acc, item) => {
      const { word } = item;
      const imageId = word.image_id ?? "unknown_image";
      const receiptId = word.receipt_id ?? "unknown_receipt";

      if (!acc[imageId]) {
        acc[imageId] = {};
      }
      if (!acc[imageId][receiptId]) {
        acc[imageId][receiptId] = [];
      }
      acc[imageId][receiptId].push(item);

      return acc;
    }, {} as { [imageId: string]: { [receiptId: string]: typeof receiptTags.payload } });

    return grouped;
  };

  // Toggle expand/collapse for a given receipt (within a given image)
  const toggleExpandReceipt = (imageId: string, receiptId: string) => {
    setExpandedReceipts((prev) => ({
      ...prev,
      [imageId]: {
        ...prev[imageId],
        [receiptId]: !prev[imageId]?.[receiptId],
      },
    }));
  };

  const groupedData = getGroupedData();

  return (
    <div>
      <h1>Receipt Validation</h1>

      {/* TAG SELECTOR */}
      {loadingTags ? (
        <div style={{ display: "flex", justifyContent: "center" }}>
          <p>Loading tags...</p>
        </div>
      ) : (
        <div style={{ display: "flex", justifyContent: "center" }}>
          <select id="tag-select" value={selectedTag} onChange={handleTagChange}>
            {tags.map((tag, index) => (
              <option key={index} value={tag}>
                {tag}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* RECEIPT WORD TAGS */}
      {selectedTag && (
        <div style={{ marginTop: "20px" }}>
          <h2>Word Tags for "{selectedTag}"</h2>
          {loadingReceiptTags ? (
            <p>Loading receipt word tags...</p>
          ) : receiptTags && receiptTags.payload && receiptTags.payload.length > 0 ? (
            <div>
              {Object.entries(groupedData).map(([imageId, receipts]) => {
                return (
                  <div key={imageId}>
                    {Object.entries(receipts).map(([receiptId, items]) => {
                      const isExpanded =
                        expandedReceipts[imageId]?.[receiptId] || false;

                      // Create a single string of all words for the summary
                      const allWords = items.map((item) => item.word.text).join(" ");
                      // Compute an overall "?" / "✗" / "✓" for the entire receipt
                      const overallStatus = getOverallValidationStatus(items);

                      return (
                        <div
                          key={receiptId}
                          style={{
                            border: "1px solid var(--text-color)",
                            borderRadius: "4px",
                            marginBottom: "10px",
                            overflow: "hidden",
                          }}
                        >
                          {/* Summary row with arrow on LEFT and status symbol on RIGHT */}
                          <div
                            style={{
                              padding: "10px",
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                              cursor: "pointer",
                            //   backgroundColor: "#f8f8f8",
                            }}
                            onClick={() => toggleExpandReceipt(imageId, receiptId)}
                          >
                            {/* LEFT side: arrow + words */}
                            <div style={{ display: "flex", alignItems: "center" }}>
                              <span
                                style={{
                                  marginRight: "8px",
                                  transition: "transform 0.3s",
                                  transform: isExpanded
                                    ? "rotate(90deg)"
                                    : "rotate(0deg)",
                                }}
                              >
                                ▶
                              </span>
                              <span>{allWords}</span>
                            </div>
                            {/* RIGHT side: overall status symbol */}
                            <strong>{overallStatus}</strong>
                          </div>

                          {/* Expanded detail rows */}
                          {isExpanded && (
                            <div style={{ padding: "10px" }}>
                              {items.map((item, idx) => (
                                <div
                                  key={idx}
                                  style={{
                                    border: "1px solid var(--text-color)",
                                    borderRadius: "4px",
                                    marginBottom: "10px",
                                    overflow: "hidden",
                                  }}
                                >
                                  {/* Word row (text on left, ?/X/check on the right) */}
                                  <div
                                    style={{
                                      padding: "10px",
                                      display: "flex",
                                      justifyContent: "space-between",
                                      alignItems: "center",
                                    }}
                                  >
                                    <strong>{item.word.text}</strong>
                                    <strong>
                                      {item.tag.validated === null
                                        ? "?"
                                        : item.tag.validated
                                        ? "✓"
                                        : "✗"}
                                    </strong>
                                  </div>
                                  {/* Additional metadata */}
                                  <div
                                    style={{
                                      padding: "10px",
                                      borderTop: "1px solid var(--text-color)",
                                    }}
                                  >
                                    <div>
                                      <strong>Tag:</strong> {item.tag.tag}
                                    </div>
                                    <div>
                                      <strong>Added:</strong>{" "}
                                      {formatTimestamp(item.tag.timestamp_added)}
                                    </div>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          ) : (
            <p>No receipt word tags available.</p>
          )}
        </div>
      )}
    </div>
  );
}

export default ReceiptValidation;