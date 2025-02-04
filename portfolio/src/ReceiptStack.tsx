// ReceiptStack.tsx
import React, { useEffect, useState } from "react";
import { fetchReceipts } from "./api";
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
  const maxReceipts = 40;
  const pageSize = 20;

  // Animate new items as they are added to `receipts`
  const transitions = useTransition(receipts, {
    from: { opacity: 0, transform: "translate(0px, -50px) rotate(0deg)" },
    enter: (item, index) => {
      const rotation = rotations[index] ?? 0;
      // Example offset so each receipt is slightly fanned out
      const topOffset = (Math.random() > 0.5 ? 1 : -1) * index * 1.5;
      const leftOffset = (Math.random() > 0.5 ? 1 : -1) * index * 1.5;
      return {
        opacity: 1,
        transform: `translate(${leftOffset}px, ${topOffset}px) rotate(${rotation}deg)`,
        delay: index * 100,
      };
    },
    keys: (item) => item.id, // Use a stable key (e.g. "receipt.id")
  });

  useEffect(() => {
    const loadAllReceipts = async () => {
      let lastEvaluatedKey: any | undefined; // `any` type if your key might be an object
      let totalFetched = 0;

      // We'll accumulate all receipts & rotations locally
      const allReceipts: Receipt[] = [];
      const allRotations: number[] = [];

      while (totalFetched < maxReceipts) {
        const response: ReceiptApiResponse = await fetchReceipts(
          pageSize,
          lastEvaluatedKey
        );

        const newReceipts = response.receipts || [];
        // Add newly fetched receipts to our local arrays
        allReceipts.push(...newReceipts);
        allRotations.push(
          ...newReceipts.map(() => Math.random() * 60 - 30)
        );

        totalFetched += newReceipts.length;

        // If there's no further key, we've fetched all pages
        if (!response.lastEvaluatedKey) {
          break;
        }
        // Otherwise, set up for the next page
        lastEvaluatedKey = response.lastEvaluatedKey;
      }

      // Now do a one-time set of receipts and rotations
      setReceipts(allReceipts.slice(0, maxReceipts));
      setRotations(allRotations.slice(0, maxReceipts));
    };

    loadAllReceipts();
  }, []);

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