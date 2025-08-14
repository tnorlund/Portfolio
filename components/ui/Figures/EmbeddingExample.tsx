import React from "react";
import { useSpring, animated } from "@react-spring/web";
import useOptimizedInView from "../../../hooks/useOptimizedInView";
import dynamic from "next/dynamic";

/** Helper that produces an array of 5 random numbers in the range [0, 1]. */
const generateEmbedding: () => number[] = () =>
  Array.from({ length: 5 }, () => parseFloat(Math.random().toFixed(2)));

type EmbeddingRowProps = {
  initial: string;
  embedding: number[] | null;
};

/**
 * Renders one row.  It starts by displaying the original string
 * and, once an embedding is supplied, fades in the mock vector.
 */
const EmbeddingRow: React.FC<EmbeddingRowProps> = ({ initial, embedding }) => {
  const [display, setDisplay] = React.useState<string>(initial);

  // Fade‑in spring that re‑triggers whenever the displayed text changes.
  const [rowSpring, api] = useSpring(() => ({
    opacity: 1, // Start visible
    config: { tension: 120, friction: 14 },
  }));

  // Handle initial display and embedding transitions
  React.useEffect(() => {
    if (embedding === null) {
      // Back to the original text at the start of each cycle
      setDisplay(initial);
      api.start({ opacity: 1 });
    } else {
      // When embedding is received, fade out → smoothly fade in vector
      api.start({
        opacity: 0,
        config: { tension: 200, friction: 20 }, // Fade out timing
        onRest: () => {
          setDisplay(`[${embedding.map((n) => n.toFixed(2)).join(", ")}]`);
          api.start({
            opacity: 1,
            config: { tension: 200, friction: 20 }, // Same timing for fade in
          });
        },
      });
    }
  }, [embedding, initial, api]);

  return (
    <animated.div
      style={{
        marginBottom: 4,
        fontFamily: "monospace",
        whiteSpace: "pre",
        padding: "2px 4px",
        ...rowSpring,
      }}
    >
      {display}
    </animated.div>
  );
};

/**
 * Displays a list of strings that are each "embedded" into a
 * 5‑value vector.  Each conversion is staggered so the rows
 * transform one‑after‑another once the component scrolls into view.
 */
const EmbeddingExampleComponent: React.FC = () => {
  const [mounted, setMounted] = React.useState(false);
  const strings = React.useMemo(
    () => [
      "Milk",
      "$5.99",
      "12/12/2024",
      "12:00 PM",
      "123 Main St",
      "Anytown, USA",
      "12345",
    ],
    []
  );

  // Cycle counter to allow the animation to restart
  const [cycle, setCycle] = React.useState(0);

  // Spring that controls the overall opacity for smooth cycle transitions
  const [wrapperSpring, wrapperApi] = useSpring(() => ({ opacity: 1 }));

  // IntersectionObserver to trigger the sequence when visible
  const [ref, inView] = useOptimizedInView({ threshold: 0.3 });

  // Track per‑row embedding (null until generated)
  const [embeddings, setEmbeddings] = React.useState<Array<number[] | null>>(
    Array(strings.length).fill(null)
  );

  React.useEffect(() => {
    setMounted(true);
  }, []);

  // Generate embeddings sequentially once the block is in view
  React.useEffect(() => {
    if (!inView || !mounted) return;

    const timers = strings.map(
      (_, idx) =>
        setTimeout(() => {
          setEmbeddings((prev) => {
            if (prev[idx]) return prev; // already set
            const next = [...prev];
            next[idx] = generateEmbedding();
            return next;
          });
        }, (idx + 1) * 1000) // start 1 s after fade‑in, then 1 s stagger
    );

    return () => timers.forEach(clearTimeout);
  }, [inView, strings, cycle, mounted]);

  // Once every row has received an embedding, fade out and restart cycle
  React.useEffect(() => {
    if (embeddings.every((e) => e !== null)) {
      // Give the user a brief moment to see the completed vectors
      const delay = setTimeout(() => {
        // Fade out entire component
        wrapperApi.start({
          opacity: 0,
          onRest: () => {
            // Reset embeddings and restart cycle
            setEmbeddings(Array(strings.length).fill(null));
            setCycle((c) => c + 1);
            // Fade back in with original words
            wrapperApi.start({ opacity: 1 });
          },
        });
      }, 2000); // 2 s pause before fading out

      return () => clearTimeout(delay);
    }
  }, [embeddings, strings, wrapperApi]);

  if (!mounted) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          minHeight: "200px",
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
          }}
        >
          {strings.map((str, idx) => (
            <div
              key={idx}
              style={{
                marginBottom: 4,
                fontFamily: "monospace",
                padding: "2px 4px",
              }}
            >
              {str}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", justifyContent: "center" }}>
      <animated.div
        ref={ref}
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          ...wrapperSpring,
        }}
      >
        {strings.map((str, idx) => (
          <EmbeddingRow
            key={`${cycle}-${idx}`}
            initial={str}
            embedding={embeddings[idx]}
          />
        ))}
      </animated.div>
    </div>
  );
};

// Export as client-only component
const EmbeddingExample = dynamic(
  () => Promise.resolve(EmbeddingExampleComponent),
  {
    ssr: false,
  }
);

export default EmbeddingExample;
