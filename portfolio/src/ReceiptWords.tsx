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

  // State to keep track of how many degrees the button is rotated
  const [rotation, setRotation] = useState<number>(0);

  const currentTag = TAGS[tagIndex];

  const handleNextTag = () => {
    // When switching tags, reset the words
    setWords([]);
    setHistogramWords([]);
    setTagIndex((prevIndex) => (prevIndex + 1) % TAGS.length);
  };

  /**
   * Animate the button using the current `rotation` state.
   * Each time `rotation` changes, the button smoothly rotates to the new angle.
   * Increased tension for a snappier feel.
   */
  const buttonSpring = useSpring({
    transform: `rotate(${rotation}deg)`,
    config: { tension: 300, friction: 20 },
  });

  useEffect(() => {
    const loadAllWords = async () => {
      setLoading(true);
      try {
        let lastEvaluatedKey: string | undefined = undefined;
        let allWords: ReceiptWord[] = [];
        
        // Paginate until no more results
        do {
          const response: ReceiptWordsApiResponse = await fetchReceiptWords(
            currentTag,
            200,
            lastEvaluatedKey
          );
          allWords = [...allWords, ...response.words];
          lastEvaluatedKey = response.lastEvaluatedKey;
        } while (lastEvaluatedKey);

        // Update all state at once after collecting all words
        setWords(allWords);
        setHistogramWords(allWords);
        setRotation(0);
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
      <div
        style={{
          marginBottom: "1rem",
          textAlign: "center",
          position: "relative",
          zIndex: 10,
        }}
      >
        <animated.button
          // onClick is not triggered when disabled during loading.
          // Alternatively: onClick={loading ? undefined : handleNextTag}
          onClick={handleNextTag}
          style={buttonSpring}
          disabled={loading}
        >
          {loading ? "Loading..." : TAG_LABELS[currentTag]}
        </animated.button>
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