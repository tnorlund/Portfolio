import { useState, useEffect } from "react";
import { fetchWordTagList, fetchReceiptWordTags } from "./api";
import { ReceiptWordTagsApiResponse } from "./interfaces"; // adjust import if needed

function ReceiptValidation() {
  const [tags, setTags] = useState<string[]>([]);
  const [selectedTag, setSelectedTag] = useState<string>("");
  const [loadingTags, setLoadingTags] = useState<boolean>(true);
  const [receiptTags, setReceiptTags] = useState<ReceiptWordTagsApiResponse | null>(null);
  const [loadingReceiptTags, setLoadingReceiptTags] = useState<boolean>(false);

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
          <h2>Word Tags</h2>
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
                    padding: "10px",
                    marginBottom: "10px",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <div>
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