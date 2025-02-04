// ReceiptStack.tsx
import React, { useEffect, useState } from "react";
import { fetchReceipts } from "./api";
import { Receipt, ReceiptApiResponse } from "./interfaces";
import { useInView } from "react-intersection-observer";
import { useSprings, animated } from "@react-spring/web";

const isDevelopment = process.env.NODE_ENV === "development";

const ReceiptStack: React.FC = () => {
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [rotations, setRotations] = useState<number[]>([]);
  const [ref, inView] = useInView({
    threshold: 0.1, 
    triggerOnce: true, 
  });

  // Set your maximum number of receipts and page size.
  const maxReceipts = 25;
  const pageSize = 5;

  useEffect(() => {
    const loadReceipts = async () => {
      let allReceipts: Receipt[] = [];
      let lastEvaluatedKey: string | undefined = undefined;

      try {
        // Loop until we've fetched maxReceipts or there are no more pages.
        do {
          const response: ReceiptApiResponse = await fetchReceipts(pageSize, lastEvaluatedKey);
          allReceipts = [...allReceipts, ...response.receipts];
          lastEvaluatedKey = response.lastEvaluatedKey;
        } while (allReceipts.length < maxReceipts && lastEvaluatedKey);

        // If we got more receipts than needed, slice the list.
        setReceipts(allReceipts.slice(0, maxReceipts));

        // Generate random rotations for each receipt.
        const newRotations = allReceipts.slice(0, maxReceipts).map(() => Math.random() * 60 - 25);
        setRotations(newRotations);
      } catch (error) {
        console.error("Error fetching receipts:", error);
      }
    };

    loadReceipts();
  }, []);

  const [springs, api] = useSprings(receipts.length, (index) => ({
    from: { opacity: 0, transform: "translate(0px, -50px) rotate(0deg)" },
    to: { opacity: 0, transform: "translate(0px, -50px) rotate(0deg)" },
  }));

  useEffect(() => {
    if (inView && receipts.length > 0) {
      api.start((index) => {
        const rotation = rotations[index] || 0;
        const topOffset = (Math.random() > 0.5 ? 1 : -1) * index * 2;
        const leftOffset = (Math.random() > 0.5 ? 1 : -1) * index * 2;
        return {
          opacity: 1,
          transform: `translate(${leftOffset}px, ${topOffset}px) rotate(${rotation}deg)`,
          delay: index * 150, // 150ms stagger between receipts
        };
      });
    }
  }, [inView, api, rotations, receipts.length]);

  return (
    <div
      ref={ref}
      style={{
        width: "100%",
        display: "flex",
        justifyContent: "center",
        marginTop: "4rem",
      }}
    >
      <div
        style={{
          position: "relative",
          width: "150px",
          minHeight: "475px",
        }}
      >
        {springs.map((styleProps, index) => {
          const receipt = receipts[index];
          if (!receipt) return null;
          const cdnUrl = isDevelopment
            ? `https://dev.tylernorlund.com/${receipt.cdn_s3_key}`
            : `https://www.tylernorlund.com/${receipt.cdn_s3_key}`;
          return (
            <animated.div
              key={receipt.id || index}
              style={{
                position: "absolute",
                width: "100px",
                border: "1px solid #ccc",
                backgroundColor: "#fff",
                boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
                ...styleProps,
              }}
            >
              <img
                src={cdnUrl}
                alt={`Receipt ${receipt.id}`}
                style={{
                  width: "100%",
                  height: "auto",
                  display: "block",
                }}
              />
            </animated.div>
          );
        })}
      </div>
    </div>
  );
};

export default ReceiptStack;