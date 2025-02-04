// ReceiptStack.tsx
import React, { useEffect, useState } from "react";
import { fetchReceipts } from "./api"; // Make sure this is your actual API call
import { Receipt, ReceiptApiResponse } from "./interfaces";
import { useInView } from "react-intersection-observer";
import { useTransition, animated } from "@react-spring/web";

const isDevelopment = process.env.NODE_ENV === "development";

const ReceiptStack: React.FC = () => {
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [rotations, setRotations] = useState<number[]>([]);

  // Only trigger "in view" once, so the stack won't re-animate on scroll in/out
  const [ref] = useInView({
    threshold: 0.1,
    triggerOnce: true,
  });

  // Customize these to your needs
  const maxReceipts = 100;
  const pageSize = 5;

  // Animate new items as they are added to `receipts`
  const transitions = useTransition(receipts, {
    from: { opacity: 0, transform: "translate(0px, -50px) rotate(0deg)" },
    enter: (item, index) => {
      const rotation = rotations[index] ?? 0;
      // Example offset so each receipt is slightly fanned out
      const topOffset = (Math.random() > 0.5 ? 1 : -1) * index * 2;
      const leftOffset = (Math.random() > 0.5 ? 1 : -1) * index * 2;
      return {
        opacity: 1,
        transform: `translate(${leftOffset}px, ${topOffset}px) rotate(${rotation}deg)`,
        delay: index * 100,
      };
    },
    keys: (item) => item.id, // Use a stable key (e.g. "receipt.id")
  });

  useEffect(() => {
    const loadReceipts = async () => {
      let lastEvaluatedKey: string | undefined; // Start undefined
      let totalFetched = 0;

      while (totalFetched < maxReceipts) {
        // Pass lastEvaluatedKey to the fetch function
        const response: ReceiptApiResponse = await fetchReceipts(
          pageSize,
          lastEvaluatedKey
        );

        // If your server has receipts in `response.receipts`,
        // and next page token in `response.lastEvaluatedKey`, do:
        const newReceipts = response.receipts || [];
        setReceipts((prev) => [...prev, ...newReceipts].slice(0, maxReceipts));

        // Create random rotation for each newly added receipt
        setRotations((prev) => [
          ...prev,
          ...newReceipts.map(() => Math.random() * 60 - 30),
        ]);

        totalFetched += newReceipts.length;

        // If there's no lastEvaluatedKey in the response, we have no more pages
        if (!response.lastEvaluatedKey) {
          break;
        }
        lastEvaluatedKey = response.lastEvaluatedKey;

        // Delay a bit so you can see the new items animate in
        await new Promise((resolve) => setTimeout(resolve, 300));
      }
    };

    loadReceipts();
  }, []);

  return (
    <div
      ref={ref} // Use IntersectionObserver to animate only once in view
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
        {transitions((style, receipt) => {
          if (!receipt) return null;
          const cdnUrl = isDevelopment
            ? `https://dev.tylernorlund.com/${receipt.cdn_s3_key}`
            : `https://www.tylernorlund.com/${receipt.cdn_s3_key}`;
          return (
            <animated.div
              key={receipt.id}
              style={{
                position: "absolute",
                width: "100px",
                border: "1px solid #ccc",
                backgroundColor: "#fff",
                boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
                ...style,
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