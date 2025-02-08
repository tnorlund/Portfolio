import { useState, useEffect } from "react";
import { fetchWordTagList } from "./api";

function ReceiptValidation() {
  const [tags, setTags] = useState<string[]>([]);
  const [selectedTag, setSelectedTag] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    // Fetch the list of tags when the component mounts.
    fetchWordTagList()
      .then((data) => {
        console.log("Fetched word tags:", data);
        setTags(data);
      })
      .catch((error) => {
        console.error("Failed to fetch word tags:", error);
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  const handleTagChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedTag(event.target.value);
  };

  return (
    <div>
      <h1>Receipt Validation</h1>
      {loading ? (
        <div style={{ display: "flex", justifyContent: "center" }}>
        <p>Loading tags...</p>
        </div>
      ) : (
        <div style={{ display: "flex", justifyContent: "center" }}>
          <div>
            <select id="tag-select" value={selectedTag} onChange={handleTagChange}>
              {tags.map((tag, index) => (
                <option key={index} value={tag}>
                  {tag}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}
    </div>
  );
}

export default ReceiptValidation;