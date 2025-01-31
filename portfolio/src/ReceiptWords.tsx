import React, { useEffect, useState } from "react";
import { fetchReceiptWords } from "./api";
import { ReceiptWord } from "./interfaces";
import TufteBoxplot from "./TufteBoxplot";
import WordsSvgContainer from "./WordsSvgContainer";

// Dictionary of tag → friendly label
const TAG_LABELS: Record<string, string> = {
  store_name: "Store Name",
  date: "Date",
  time: "Time",
  phone_number: "Phone Number",
  total_amount: "Total Amount",
  line_item: "Line Item",
  line_item_name: "Line Item Name",
  line_item_price: "Line Item Price",
  taxes: "Taxes",
  address: "Address",
};

// The original array of DB tags
const TAGS = Object.keys(TAG_LABELS);

const ReceiptWords: React.FC = () => {
  const [words, setWords] = useState<ReceiptWord[]>([]);
  const [histogramWords, setHistogramWords] = useState<ReceiptWord[]>([]);
  const [tagIndex, setTagIndex] = useState(0);

  const currentTag = TAGS[tagIndex];

  const handleNextTag = () => {
    setTagIndex((prevIndex) => (prevIndex + 1) % TAGS.length);
  };

  useEffect(() => {
    const getWords = async () => {
      try {
        const response = await fetchReceiptWords(currentTag);
        setWords(response);
        setHistogramWords(response);
      } catch (error) {
        console.error("Error fetching words:", error);
      }
    };
    getWords();
  }, [currentTag]);

  return (
    <div style={{ margin: "1rem auto", maxWidth: "1200px" }}>
      <div style={{ marginBottom: "1rem", textAlign: "center" }}>
        {/* Use the label from the dictionary for the button text */}
        <button onClick={handleNextTag}>
          {TAG_LABELS[currentTag]}
        </button>
      </div>

      <div
        style={{
          display: "flex",
          width: "100%",
          alignItems: "stretch",
          gap: "1rem",
        }}
      >
        <div style={{ flex: 1 }}>
          <WordsSvgContainer words={words} />
        </div>

        <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
          <TufteBoxplot words={histogramWords} />
        </div>
      </div>
    </div>
  );
};

export default ReceiptWords;