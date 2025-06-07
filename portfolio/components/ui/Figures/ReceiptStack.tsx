// ReceiptStack.tsx
import React, { useEffect, useState } from "react";
import { api } from "../../../services/api";
import { Receipt, ReceiptApiResponse } from "../../../types/api";
import { useInView } from "react-intersection-observer";
import { useTransition, animated } from "@react-spring/web";

const isDevelopment = process.env.NODE_ENV === "development";

const ReceiptStack: React.FC = () => {
  const [ref, inView] = useInView({
    threshold: 0.1,
    triggerOnce: true,
  });

  const [prefetchedReceipts, setPrefetchedReceipts] = useState<Receipt[]>([]);
  const [prefetchedRotations, setPrefetchedRotations] = useState<number[]>([]);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [rotations, setRotations] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Customize these to your needs
  const maxReceipts = 40;
  const pageSize = 20;

  // Animate new items as they are added to `receipts`
  const transitions = useTransition(receipts, {
    from: { opacity: 0, transform: "translate(0px, -50px) rotate(0deg)" },
    enter: (item, index) => {
      const rotation = rotations[index] ?? 0;
      const topOffset = (Math.random() > 0.5 ? 1 : -1) * index * 1.5;
      const leftOffset = (Math.random() > 0.5 ? 1 : -1) * index * 1.5;
      return {
        opacity: 1,
        transform: `translate(${leftOffset}px, ${topOffset}px) rotate(${rotation}deg)`,
        delay: index * 100,
      };
    },
    keys: (item) => `${item.receipt_id}-${item.image_id}`,
  });

  useEffect(() => {
    const loadAllReceipts = async () => {
      setLoading(true);
      setError(null);

      let lastEvaluatedKey: any | undefined;
      let totalFetched = 0;

      const allReceipts: Receipt[] = [];
      const allRotations: number[] = [];

      try {
        while (totalFetched < maxReceipts) {
          const response: ReceiptApiResponse = await api.fetchReceipts(
            pageSize,
            lastEvaluatedKey
          );

          // Check if response has the expected structure
          if (!response || typeof response !== "object") {
            console.error("Invalid response structure:", response);
            break;
          }

          const newReceipts = response.receipts || [];

          if (!Array.isArray(newReceipts)) {
            console.error("Response receipts is not an array:", newReceipts);
            break;
          }

          allReceipts.push(...newReceipts);
          allRotations.push(...newReceipts.map(() => Math.random() * 60 - 30));

          totalFetched += newReceipts.length;

          if (!response.lastEvaluatedKey) {
            break;
          }
          lastEvaluatedKey = response.lastEvaluatedKey;
        }

        setPrefetchedReceipts(allReceipts.slice(0, maxReceipts));
        setPrefetchedRotations(allRotations.slice(0, maxReceipts));
      } catch (error) {
        console.error("Error loading receipts:", error);
        setError(
          error instanceof Error ? error.message : "Failed to load receipts"
        );
        // Set empty arrays to prevent rendering issues
        setPrefetchedReceipts([]);
        setPrefetchedRotations([]);
      } finally {
        setLoading(false);
      }
    };

    loadAllReceipts();
  }, [maxReceipts, pageSize]);

  // When the user scrolls to this component, start the animation using the
  // prefetched data
  useEffect(() => {
    if (inView && receipts.length === 0 && prefetchedReceipts.length) {
      setReceipts(prefetchedReceipts);
      setRotations(prefetchedRotations);
    }
  }, [inView, prefetchedReceipts, prefetchedRotations, receipts.length]);

  // Show loading state once in view
  if (inView && loading) {
    return (
      <div
        ref={ref}
        style={{
          width: "100%",
          display: "flex",
          justifyContent: "center",
          marginTop: "6rem",
          padding: "2rem",
        }}
      >
        <p>Loading receipts...</p>
      </div>
    );
  }

  // Show error state once in view
  if (inView && error) {
    return (
      <div
        ref={ref}
        style={{
          width: "100%",
          display: "flex",
          justifyContent: "center",
          marginTop: "6rem",
          padding: "2rem",
          color: "red",
        }}
      >
        <p>Error: {error}</p>
      </div>
    );
  }

  // Show empty state if data loaded but there are no receipts
  if (inView && !loading && receipts.length === 0) {
    return (
      <div
        ref={ref}
        style={{
          width: "100%",
          display: "flex",
          justifyContent: "center",
          marginTop: "6rem",
          padding: "2rem",
        }}
      >
        <p>No receipts found</p>
      </div>
    );
  }

  return (
    <div
      ref={ref}
      style={{
        width: "100%",
        display: "flex",
        justifyContent: "center",
        marginTop: "6rem",
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
              style={{
                position: "absolute",
                width: "100px",
                border: "1px solid #ccc",
                backgroundColor: "var(--background-color)",
                boxShadow: "0 2px 6px rgba(0,0,0,0.2)",
                ...style,
              }}
            >
              <img
                src={cdnUrl}
                alt={`Receipt ${receipt.receipt_id}`}
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
