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
  // Set triggerOnce: true so the "in view" state is only set the first time.
  const [ref] = useInView({
    threshold: 0.1,
    triggerOnce: true,
  });

  const maxReceipts = 100;
  const pageSize = 5;

  // useTransition handles animating items as they are added to the receipts array.
  const transitions = useTransition(receipts, {
    from: { opacity: 0, transform: "translate(0px, -50px) rotate(0deg)" },
    enter: (item, index) => {
      const rotation = rotations[index] ?? 0;
      const topOffset = (Math.random() > 0.5 ? 1 : -1) * index * 2;
      const leftOffset = (Math.random() > 0.5 ? 1 : -1) * index * 2;
      return {
        opacity: 1,
        transform: `translate(${leftOffset}px, ${topOffset}px) rotate(${rotation}deg)`,
        delay: index * 150,
      };
    },
    keys: (item) => item.id,
  });

  useEffect(() => {
    const loadReceipts = async () => {
      let lastEvaluatedKey: string | undefined = undefined;
      let totalFetched = 0;

      while (totalFetched < maxReceipts) {
        const response: ReceiptApiResponse = await fetchReceipts(pageSize, lastEvaluatedKey);

        // Update state incrementally so new pages animate in via useTransition.
        setReceipts((prev) => {
          const updated = [...prev, ...response.receipts];
          return updated.slice(0, maxReceipts);
        });
        setRotations((prev) => [
          ...prev,
          ...response.receipts.map(() => Math.random() * 60 - 30),
        ]);

        totalFetched += response.receipts.length;
        if (!response.lastEvaluatedKey) break;
        lastEvaluatedKey = response.lastEvaluatedKey;

        // Delay briefly so the UI can update and animate the new receipts
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
    };

    loadReceipts();
  }, []);

  return (
    <div
      ref={ref} // This container triggers the "in view" event once.
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
        {transitions((style, receipt, _, index) => {
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