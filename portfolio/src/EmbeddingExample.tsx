import React from "react";
import { useSpring, animated } from "@react-spring/web";
import { useInView } from "react-intersection-observer";

/** Helper that produces an array of 5 random numbers in the range [0, 1]. */
const generateEmbedding = () =>
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
    opacity: 0,
    config: { tension: 120, friction: 14 },
  }));

  React.useEffect(() => {
    if (embedding === null) {
      // Back to the original text at the start of each cycle
      setDisplay(initial);
    } else {
      // Show the mock vector once it’s generated
      setDisplay(`[${embedding.map((n) => n.toFixed(2)).join(", ")}]`);
    }
  }, [embedding, initial]);

  React.useEffect(() => {
    api.start({ opacity: 1 });
  }, [display, api]);

  return (
    <animated.div
      style={{
        marginBottom: 4,
        fontFamily: "monospace",
        whiteSpace: "pre",
        ...rowSpring,
      }}
    >
      {display}
    </animated.div>
  );
};

/**
 * Displays a list of strings that are each “embedded” into a
 * 5‑value vector.  Each conversion is staggered so the rows
 * transform one‑after‑another once the component scrolls into view.
 */
const EmbeddingExample: React.FC = () => {
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

  // Spring that controls the overall opacity of the block so we can
  // fade everything out (to 0) and back in (to 1) between cycles.
  const [wrapperSpring, wrapperApi] = useSpring(() => ({ opacity: 1 }));

  // IntersectionObserver to trigger the sequence when visible
  const [ref, inView] = useInView({ threshold: 0.3 });

  // Track per‑row embedding (null until generated)
  const [embeddings, setEmbeddings] = React.useState<Array<number[] | null>>(
    Array(strings.length).fill(null)
  );

  // Generate embeddings sequentially once the block is in view
  React.useEffect(() => {
    if (!inView) return;

    const timers = strings.map(
      (_, idx) =>
        setTimeout(() => {
          setEmbeddings((prev) => {
            if (prev[idx]) return prev; // already set
            const next = [...prev];
            next[idx] = generateEmbedding();
            return next;
          });
        }, (idx + 1) * 1000) // start 1 s after fade‑in, then 1 s stagger
    );

    return () => timers.forEach(clearTimeout);
  }, [inView, strings, cycle]);

  // Once every row has received an embedding, start the fade‑out,
  // reset state, and kick off the next cycle.
  React.useEffect(() => {
    if (embeddings.every((e) => e !== null)) {
      // Give the user a brief moment to see the completed vectors
      const delay = setTimeout(() => {
        wrapperApi.start({
          opacity: 0,
          onRest: () => {
            // Reset and restart
            setEmbeddings(Array(strings.length).fill(null));
            setCycle((c) => c + 1);
            wrapperApi.start({ opacity: 1 });
          },
        });
      }, 1000); // 1 s pause before fading out

      return () => clearTimeout(delay);
    }
  }, [embeddings, wrapperApi, strings]);

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
          <EmbeddingRow key={idx} initial={str} embedding={embeddings[idx]} />
        ))}
      </animated.div>
    </div>
  );
};

export default EmbeddingExample;
