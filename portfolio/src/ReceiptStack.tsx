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
  const [ref, inView] = useInView({
    threshold: 0.1,
    // We want to animate new pages even if the component is already in view.
    triggerOnce: false,
  });

  const maxReceipts = 100;
  const pageSize = 5;

  // useTransition handles animating items as they are added to the receipts array.
  const transitions = useTransition(receipts, {
    // When a receipt is added, start off hidden and above the stack.
    from: { opacity: 0, transform: "translate(0px, -50px) rotate(0deg)" },
    // When a receipt enters, animate it in with a slight stagger.
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

        // Update state incrementally so new pages trigger an animation.
        setReceipts((prev) => {
          const updated = [...prev, ...response.receipts];
          return updated.slice(0, maxReceipts);
        });
        setRotations((prev) => [
          ...prev,
          ...response.receipts.map(() => Math.random() * 60 - 25),
        ]);

        totalFetched += response.receipts.length;
        if (!response.lastEvaluatedKey) break;
        lastEvaluatedKey = response.lastEvaluatedKey;
      }
    };

    // Start fetching receipts immediately.
    loadReceipts();
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
        {transitions((style, receipt, t, index) => {
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