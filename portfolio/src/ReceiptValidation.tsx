import { useState, useEffect } from "react";
import { fetchWordTagList, fetchReceiptWordTags } from "./api";
import { ReceiptWordTagsApiResponse } from "./interfaces"; // adjust import if needed

// Helper function to format the timestamp
function formatTimestamp(timestamp: string): string {
    const date = new Date(timestamp);
    // Customize the format as needed; this example uses the local date and time format.
    return date.toLocaleString();
  }

function ReceiptValidation() {
  const [tags, setTags] = useState<string[]>([]);
  const [selectedTag, setSelectedTag] = useState<string>("");
  const [loadingTags, setLoadingTags] = useState<boolean>(true);
  const [receiptTags, setReceiptTags] = useState<ReceiptWordTagsApiResponse | null>(null);
  const [loadingReceiptTags, setLoadingReceiptTags] = useState<boolean>(false);
  // Expanded state object where key is the index of the tag item
  const [expanded, setExpanded] = useState<{ [key: number]: boolean }>({});

  // Fetch the list of word tags when the component mounts.
  useEffect(() => {
    fetchWordTagList()
      .then((data) => {
        console.log("Fetched word tags:", data);
        setTags(data);
        // Optionally, set a default tag if available.
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

  // Whenever the selectedTag changes, fetch the receipt word tags.
  useEffect(() => {
    if (selectedTag) {
      setLoadingReceiptTags(true);
      // Here we pass the selected tag and a limit (for example, 5). Adjust the limit as needed.
      fetchReceiptWordTags(selectedTag, 5)
        .then((data) => {
          console.log("Fetched receipt word tags:", data);
          setReceiptTags(data);
          // Reset any expanded items when a new tag is selected.
          setExpanded({});
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

  const toggleExpand = (index: number) => {
    setExpanded((prev) => ({
      ...prev,
      [index]: !prev[index],
    }));
  };

  return (
    <div>
      <h1>Receipt Validation</h1>
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

      {selectedTag && (
        <div style={{ marginTop: "20px" }}>
          <h2>Word Tags for "{selectedTag}"</h2>
          {loadingReceiptTags ? (
            <p>Loading receipt word tags...</p>
          ) : receiptTags && receiptTags.payload && receiptTags.payload.length > 0 ? (
            <div>
              {receiptTags.payload.map((item, index) => (
                <div
                  key={index}
                  style={{
                    border: "1px solid var(--text-color)",
                    borderRadius: "4px",
                    marginBottom: "10px",
                    overflow: "hidden",
                  }}
                >
                  {/* Header area with arrow, word text, and validated status */}
                  <div
                    style={{
                      padding: "10px",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      cursor: "pointer",
                    }}
                    onClick={() => toggleExpand(index)}
                  >
                    <div style={{ display: "flex", alignItems: "center" }}>
                      <span
                        style={{
                          marginRight: "10px",
                          transition: "transform 0.3s",
                          transform: expanded[index] ? "rotate(90deg)" : "rotate(0deg)",
                          userSelect: "none",
                        }}
                      >
                        ▶
                      </span>
                      <strong>{item.word.text}</strong>
                    </div>
                    <div>
                      <strong>
                        {item.tag.validated === null
                          ? "?"
                          : item.tag.validated
                          ? "✓"
                          : "✗"}
                      </strong>
                    </div>
                  </div>
                  {/* Expanded content */}
                  {expanded[index] && (
                    <div style={{ padding: "10px", borderTop: "1px solid var(--text-color)" }}>
                      <div>
                        <strong>Tag:</strong> {item.tag.tag}
                      </div>
                      <div>
                        <strong>Added:</strong> {formatTimestamp(item.tag.timestamp_added)}
                      </div>
                      {/* Add additional details here as needed */}
                    </div>
                  )}
                </div>
              ))}
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