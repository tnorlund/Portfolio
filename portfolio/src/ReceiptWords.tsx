import React, { useEffect, useState } from "react";
import { useSpring, animated } from "@react-spring/web";
import { fetchReceiptWords } from "./api";
import { ReceiptWord, ReceiptWordsApiResponse } from "./interfaces";
import TufteBoxplot from "./TufteBoxplot";
import WordsSvgContainer from "./WordTagsVisualization";

// Dictionary mapping DB tag names to friendly labels
const TAG_LABELS: Record<string, string> = {
  store_name: "Store Name",
  date: "Date",
  time: "Time",
  phone_number: "Phone Number",
  total_amount: "Total Amount",
  line_item_name: "Line Item Name",
  line_item_price: "Line Item Price",
  taxes: "Taxes",
  address: "Address",
};

const TAGS = Object.keys(TAG_LABELS);

const ReceiptWords: React.FC = () => {
  const [words, setWords] = useState<ReceiptWord[]>([]);
  const [histogramWords, setHistogramWords] = useState<ReceiptWord[]>([]);
  const [tagIndex, setTagIndex] = useState(0);
  const [loading, setLoading] = useState<boolean>(false);

  const currentTag = TAGS[tagIndex];

  const handleNextTag = () => {
    // When switching tags, reset the words
    setWords([]);
    setHistogramWords([]);
    setTagIndex((prevIndex) => (prevIndex + 1) % TAGS.length);
  };

  // Animate the button: while loading, the button rotates continuously.
  const buttonSpring = useSpring({
    transform: loading ? "rotate(360deg)" : "rotate(0deg)",
    config: { duration: 1000 },
    loop: loading ? { reverse: false } : false,
  });

  useEffect(() => {
    const loadAllWords = async () => {
      setLoading(true);
      try {
        let lastEvaluatedKey: string | undefined = undefined;
        // Clear the current words when the tag changes.
        setWords([]);
        setHistogramWords([]);
        // Fetch pages incrementally.
        do {
          const response: ReceiptWordsApiResponse = await fetchReceiptWords(
            currentTag,
            200,
            lastEvaluatedKey
          );
          // Update the words state incrementally.
          setWords((prevWords) => [...prevWords, ...response.words]);
          setHistogramWords((prevWords) => [...prevWords, ...response.words]);
          lastEvaluatedKey = response.lastEvaluatedKey;
        } while (lastEvaluatedKey);
      } catch (error) {
        console.error("Error fetching receipt words:", error);
      } finally {
        setLoading(false);
      }
    };

    loadAllWords();
  }, [currentTag]);

  return (
    <div style={{ margin: "1rem auto", maxWidth: "1200px" }}>
      {/* Button container with high z-index so that it is always visible */}
      <div
        style={{
          marginBottom: "1rem",
          textAlign: "center",
          position: "relative",
          zIndex: 10,
        }}
      >
        <animated.button
          onClick={handleNextTag}
          style={buttonSpring}
          disabled={loading}
        >
          {loading ? "Loading..." : TAG_LABELS[currentTag]}
        </animated.button>
      </div>
      <div style={{ display: "flex", width: "100%", alignItems: "stretch", gap: "1rem" }}>
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