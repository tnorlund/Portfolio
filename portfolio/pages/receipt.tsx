import React, { useState, useEffect, useCallback } from "react";
import Head from "next/head";
import styles from "../styles/Receipt.module.css";
import { GetStaticProps } from "next";
import ClientOnly from "../components/ClientOnly";

// Import components normally - they'll be wrapped in ClientOnly
import {
  ClientImageCounts,
  ClientReceiptCounts,
  MerchantCount,
  ZDepthConstrained,
  ZDepthUnconstrained,
  UploadDiagram,
  EmbeddingExample,
  EmbeddingCoordinate,
  ReceiptStack,
  LabelValidationCount,
  ScanReceiptBoundingBox,
  ReceiptPhotoClustering,
  PhotoReceiptBoundingBox,
} from "../components/ui/Figures";
import AnimatedInView from "../components/ui/AnimatedInView";
import {
  OpenAILogo,
  PineconeLogo,
  GooglePlacesLogo,
  GithubLogo,
  HuggingFaceLogo,
  PulumiLogo,
  ReactLogo,
  NextJSLogo,
} from "../components/ui/Logos";

interface ReceiptPageProps {
  uploadDiagramChars: string[];
}

// Use getStaticProps for static generation - this runs at build time
export const getStaticProps: GetStaticProps<ReceiptPageProps> = async () => {
  // Generate random chars at build time (deterministic for each build)
  // We need 30 bits per stream, and there are up to 8 phases with multiple paths
  // Generate 240 to ensure we have enough for all possible bit streams
  const uploadDiagramChars = Array.from({ length: 120 }, () =>
    Math.random() > 0.5 ? "1" : "0"
  );

  return {
    props: {
      uploadDiagramChars,
    },
  };
};

export default function ReceiptPage({ uploadDiagramChars }: ReceiptPageProps) {
  // Remove client-side generation - now passed as prop from getStaticProps
  // const [uploadDiagramChars, setUploadDiagramChars] = useState<string[]>([]);

  // useEffect(() => {
  //   // Generate random chars client-side
  //   // We need 30 bits per stream, and there are up to 8 phases with multiple paths
  //   // Generate 240 to ensure we have enough for all possible bit streams
  //   const chars = Array.from({ length: 240 }, () =>
  //     Math.random() > 0.5 ? "1" : "0"
  //   );
  //   setUploadDiagramChars(chars);
  // }, []);

  // --- Receipt Upload State & Handlers ---
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState("");
  const [dragging, setDragging] = useState(false);
  const [apiUrl, setApiUrl] = useState("");

  useEffect(() => {
    const isDevelopment = process.env.NODE_ENV === "development";
    setApiUrl(
      isDevelopment
        ? "https://dev-upload.tylernorlund.com"
        : "https://upload.tylernorlund.com"
    );
  }, []);

  const uploadToS3Internal = useCallback(
    async (selectedFiles: File[]) => {
      if (selectedFiles.length === 0) return;
      setUploading(true);
      setMessage("");

      try {
        for (const file of selectedFiles) {
          // 1. Ask backend for a presigned PUT URL (and create the job)
          const presignRes = await fetch(`${apiUrl}/upload-receipt`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              filename: file.name,
              content_type: file.type,
            }),
          });

          if (!presignRes.ok) {
            throw new Error(
              `Failed to request upload URL for ${file.name} (status ${presignRes.status})`
            );
          }

          const { upload_url } = await presignRes.json();

          // 2. Upload the file directly to S3
          const putRes = await fetch(upload_url, {
            method: "PUT",
            headers: { "Content-Type": file.type },
            body: file,
          });

          if (!putRes.ok) {
            throw new Error(`Upload failed for file ${file.name}`);
          }
        }

        setMessage(
          `Upload successful: ${selectedFiles.map((f) => f.name).join(", ")}`
        );
        setFiles([]);
      } catch (err) {
        console.error(err);
        setMessage("Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [apiUrl]
  );

  const handleDrop = useCallback(
    async (e: DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const dt = e.dataTransfer;
      if (dt && dt.files) {
        const newFiles = Array.from(dt.files);
        setFiles((prev) => [...prev, ...newFiles]);
        await uploadToS3Internal(newFiles);
      }
    },
    [uploadToS3Internal]
  );

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragging(false);
  }, []);

  const uploadToS3 = useCallback(() => {
    uploadToS3Internal(files);
    setFiles([]);
  }, [files, uploadToS3Internal]);

  const handleFileInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) {
        const newFiles = Array.from(e.target.files);
        setFiles((prev) => [...prev, ...newFiles]);
        uploadToS3Internal(newFiles);
      }
    },
    [uploadToS3Internal]
  );

  useEffect(() => {
    window.addEventListener("dragover", handleDragOver);
    window.addEventListener("drop", handleDrop);
    window.addEventListener("dragleave", handleDragLeave);
    return () => {
      window.removeEventListener("dragover", handleDragOver);
      window.removeEventListener("drop", handleDrop);
      window.removeEventListener("dragleave", handleDragLeave);
    };
  }, [handleDrop, handleDragOver, handleDragLeave]);

  return (
    <div
      className="container"
      style={{
        position: "relative",
        minHeight: "100vh",
        backgroundColor: dragging
          ? "rgba(var(--color-blue-rgb), 0.05)"
          : "transparent",
        transition: "background-color 0.3s ease",
      }}
    >
      <Head>
        <title>Receipt | Tyler Norlund</title>
      </Head>
      {/* Upload area overlay when dragging */}
      {dragging && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            width: "100%",
            height: "100%",
            backgroundColor: "rgba(var(--color-blue-rgb), 0.1)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            pointerEvents: "none",
            border: "3px dashed var(--color-blue)",
            boxSizing: "border-box",
          }}
        >
          <div
            style={{
              background: "var(--background-color)",
              padding: "2rem",
              borderRadius: "10px",
              boxShadow: "0 4px 20px rgba(0,0,0,0.3)",
              border: "2px solid var(--color-blue)",
              fontSize: "1.5rem",
              fontWeight: "bold",
              color: "var(--color-blue)",
            }}
          >
            Drop receipt images here to upload
          </div>
        </div>
      )}

      {/* Upload button in the top section */}

      <h1>Introduction</h1>
      <p>
        I wanted to get better at using AI to automate tasks. I already use
        ChatGPT for everyday chores, but I wanted to use it on something more
        challenging: receipts. They may look deceptively simple, but each
        squeezes a lot of information into a tiny space, making it surprisingly
        difficult to decode.
      </p>

      <h2>Why Receipts?</h2>

      <p>
        Receipts are a difficult problem to solve. To turn a crumpled piece of
        paper into structuvar(--color-red) data, you have to read three layers
        of data:
      </p>
      <ul>
        <li>
          <strong>Text</strong>: The words on the receipt
        </li>
        <li>
          <strong>Layout</strong>: The layout of the receipt
        </li>
        <li>
          <strong>Meaning</strong>: The meaning of the words
        </li>
      </ul>
      <p>
        Since these layers arrive in messy, mixed formats, receipts are an ideal
        test case for learning how to use AI to automate real-world tasks. The
        rest of this post describes how I disentangle those three layers and
        stitch the answers back together.
      </p>

      <h1>From Images to Words and Coordinates</h1>

      <p>
        Every receipt image arrives as either a flat scan or a photo. I treat
        them differently because each format comes with its own shortcuts and
        challenges.
      </p>

      <h2>Scans</h2>
      <p>
        Scanned receipts are easiest to work with. They are as flat as the
        sensors used to pick up the pixel data.
      </p>

      <ClientOnly>
        <ZDepthConstrained />
      </ClientOnly>

      <p>
        With this constraint, we can say that all the text is on the same plane.
        This means that the text has the same depth. We can also say that the
        receipt is taller than it is wide. With all this information, we can
        determine which words belong to which receipt based on the X position.
      </p>

      <ScanReceiptBoundingBox />
      <h2>Photos</h2>
      <p>
        Photos are slightly more difficult to work with. The camera sensor is
        not always facing the receipt.
      </p>

      <ClientOnly>
        <ZDepthUnconstrained />
      </ClientOnly>

      <p>
        This requires a more complex algorithm to determine which words belong
        to which receipt. I used a combination of DBSCAN clustering and convex
        hull calculations to determine a warp to get the cleanest image.
      </p>

      <PhotoReceiptBoundingBox />

      <h2>Piping It Together</h2>
      <p>
        My Macbook has proven to be the best at determining words and
        coordinates. I developed a way to pipe my laptop to the cloud so that I
        can efficiently and cost-effectively structure the data.
      </p>

      <ClientOnly>
        <UploadDiagram chars={uploadDiagramChars} />
      </ClientOnly>

      <p>
        This architecture allows me to scale horizontally by adding another Mac.
        I can scale vertically by paying for more cloud compute. The current
        architecture allows me to operate for free!
      </p>

      <h1>Semantic Labeling</h1>
      <p>
        In order to explain the text on the receipt, I needed to explain how the
        words semantically relate to one another. But before I can explain, we
        have to understand what embedding is.
      </p>
      <h2>Embedding</h2>
      <p>
        I had never used embeddings before. They are not exactly new, but they
        have become widely available in the last few years. What embeddings
        offer is{" "}
        <i>
          the ability to discover connections between things at previously
          impossible scales.
        </i>{" "}
      </p>
      <p>
        If someone were to ask you to embed something, what do you need? You
        start with textual representation of the thing they're asking to embed.
      </p>

      <p>What do you get back? You get a structure of numbers.</p>

      <ClientOnly>
        <EmbeddingExample />
      </ClientOnly>

      <p>
        While each input might be different, we get the same structure of
        numbers back. Here's the magic. Because we get the same structure, we
        have a way to mathematically compare two pieces of text together. But
        what do the numbers mean?
      </p>
      <h3>How To Literally Embed</h3>
      <p>
        There are many services that offer to generate embeddings, but I ended
        up going with OpenAI.
      </p>
      <ClientOnly>
        <AnimatedInView>
          <OpenAILogo />
        </AnimatedInView>
      </ClientOnly>
      <h3>Is It Expensive?</h3>
      <p>
        No. I experimented <i>a lot</i>. I developed a way to batch embeddings
        to cut costs further. This part is also free.
      </p>
      <h3>But What Do The Numbers Actually Mean?</h3>
      <p>
        So back to the question: what do the numbers mean? Let's think about
        coordinates on a map. Suppose I give you three points:
      </p>
      <table style={{ margin: "0 auto", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={{ padding: "8px 16px", borderBottom: "1px solid #ddd" }}>
              Point
            </th>
            <th
              style={{
                padding: "8px 16px",
                borderBottom: "1px solid #ddd",
                textAlign: "right",
              }}
            >
              X
            </th>
            <th
              style={{
                padding: "8px 16px",
                borderBottom: "1px solid #ddd",
                textAlign: "right",
              }}
            >
              Y
            </th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td style={{ padding: "8px 16px" }}>A</td>
            <td style={{ padding: "8px 16px", textAlign: "right" }}>{"3"}</td>
            <td style={{ padding: "8px 16px", textAlign: "right" }}>{"2"}</td>
          </tr>
          <tr>
            <td style={{ padding: "8px 16px" }}>B</td>
            <td style={{ padding: "8px 16px", textAlign: "right" }}>{"1"}</td>
            <td style={{ padding: "8px 16px", textAlign: "right" }}>{"1"}</td>
          </tr>
          <tr>
            <td style={{ padding: "8px 16px" }}>C</td>
            <td style={{ padding: "8px 16px", textAlign: "right" }}>{"-2"}</td>
            <td style={{ padding: "8px 16px", textAlign: "right" }}>{"-2"}</td>
          </tr>
        </tbody>
      </table>
      <p>
        There are 2 dimensions to this map: X and Y. Each point lives at the
        intersection of an X and Y coordinate.
      </p>
      <p>Is A closer to B or C?</p>

      <EmbeddingCoordinate />

      <p>A is closer to B.</p>
      <p>
        Here's the mental leap.{" "}
        <i>Embeddings are similar to points on a map.</i> Each number in the
        embedding is a coordinate in a complicated map. When OpenAI sends a list
        of numbers, it's telling you where that text <i>semantically</i> lives
        in that map. When we ask what the distance between two embeddings are,
        what we're really doing is asking how semantically close or far apart
        two pieces of text are.
      </p>
      <p>
        This concept of positioning items in multi-dimensional space like this,
        where related items are clustevar(--color-red) near each other, goes by
        the name of <strong>latent space</strong>.
      </p>
      <p>
        Latent space is a powerful concept. It allows us to discover connections
        between things at previously impossible scales.
      </p>
      <h2>Scaling and Optimizing Latent Space</h2>
      <p>
        Writing and reading these lists of numbers get's complicated fast. After
        some research, I found Pinecone, a vector database that allows me to
        store and retrieve embeddings.
      </p>
      <ClientOnly>
        <AnimatedInView>
          <PineconeLogo />
        </AnimatedInView>
      </ClientOnly>
      <p>
        Pinecone's real strength shows when you attach <i>meaningful</i>
        information to each embedding. The embedding by itself can telling you
        which words are similar, but adding context, store name, location, or
        even category, let's you find the semantically similar words you're
        looking for.
      </p>

      <h2>Meaningful Metadata</h2>
      <p>
        Imagine looking for the word "latte" across 10,000 receipts. Without
        context, you'll get results from latte flavovar(--color-red) cereal at
        grocery stores, expensive drinks at coffee shops, and even brown
        colovar(--color-red) paint from hardware stores.
      </p>
      <p>With context, you can filter out the results that don't make sense.</p>
      <p>
        I used OpenAI's Agents SDK with the Google Places API to get the context
        needed for rich, semantic search.
      </p>

      <ClientOnly>
        <AnimatedInView>
          <GooglePlacesLogo />
        </AnimatedInView>
      </ClientOnly>

      <MerchantCount />

      <h2>Turning Semantic Search into Autonomous Labeling</h2>
      <p>
        Pinecone doesn't just help me <i>find</i> similar words, it allows me to
        act on them. After every receipt is embedded, an OpenAI agent retrieves
        the "nearest neighbors" of each unlabeled word, filtevar(--color-red) by
        the receipt's merchant-specific metadata.
      </p>
      <p>
        For the token "latte" on a Starbucks receipt, an agent pulls
        semantically similar words from other Starbucks receipts and asks:
      </p>
      <blockquote>
        "Given these examples and surrounding words, what label would best
        describe <strong>latte</strong>?"
      </blockquote>

      <p>
        The agent then validates the proposed label using custom rules, similar
        examples where the word is correctly labeled with that label, and
        examples where the word is incorrectly labeled.
      </p>

      <LabelValidationCount />

      <h2>The Feedback Loop</h2>
      <p>
        This process is repeated to continuously improve the accuracy of the
        labels. As part of this loop, I use <strong>RAGAS</strong> to evaluate
        how faithful and relevant the model's responses are to the retrieved
        context.
      </p>

      <h1>Conclusion</h1>
      <h2>Moving Fast and Breaking Things</h2>
      <p>
        This project was a great learning experience. The best way for me to
        learn is by actually doing. Experimenting with different tools and
        techniques allowed me to reflect on what worked and what didn't.
      </p>
      <p>I used Github and Pulumi to manage the cloud and code.</p>
      <div className={styles.logosContainer}>
        <ClientOnly>
          <AnimatedInView>
            <GithubLogo />
          </AnimatedInView>
        </ClientOnly>
        <ClientOnly>
          <AnimatedInView>
            <PulumiLogo />
          </AnimatedInView>
        </ClientOnly>
      </div>
      <p>
        I got my build time down to ~10 seconds. This allows me to make a change
        locally and get feedback in seconds. Github allows me to track my
        changes and deploy to production.
      </p>
      <p>
        I'm using React to build the frontend. It's a great way to get started,
        but I wanted to continue to increase my tech stack. I ended up porting
        to NextJS.
      </p>
      <ClientOnly>
        <AnimatedInView replacement={<NextJSLogo />} replaceAfterMs={1000}>
          <ReactLogo />
        </AnimatedInView>
      </ClientOnly>
      <p>
        Moving to NextJS was really easy. Using a combination of Cursor and
        OpenAI's Codex allowed me to move this from one framework to another
        with minimal effort.
      </p>

      <h2>Training a Custom Model</h2>
      <p>
        I'm currently training a custom model to improve the process of getting
        an image and structuring the data. Having to query a database of similar
        words works for large datasets, but there's room here for a simple model
        that can run on my laptop. I've been playing with a few models on
        Hugging Face.
      </p>
      <ClientOnly>
        <AnimatedInView>
          <HuggingFaceLogo />
        </AnimatedInView>
      </ClientOnly>

      <h2>A New Era of Coding</h2>
      <p>
        Artificial intelligence is advancing quickly and changing how we write
        software. However, no matter how smart computers become, we still need
        solid engineering practices, clever problem-solving, and expert
        knowledge. I'm excited to see a future where people and AI work together
        to make programming faster, smarter, and easier for everyone.
      </p>

      <div style={{ marginBottom: "2rem", textAlign: "center" }}>
        <label htmlFor="file-upload" style={{ display: "inline-block" }}>
          <input
            id="file-upload"
            type="file"
            multiple
            accept="image/*"
            onChange={handleFileInput}
            style={{ display: "none" }}
          />
          <button
            style={{
              cursor: "pointer",
              display: "inline-block",
            }}
            onClick={() => document.getElementById("file-upload")?.click()}
          >
            📸 Upload Receipt Images
          </button>
        </label>
        <p
          style={{
            fontSize: "0.9rem",
            color: "var(--text-color)",
            opacity: 0.7,
            marginTop: "0.5rem",
          }}
        >
          You can also drag and drop images anywhere on this page
        </p>
      </div>

      <div className={styles.logosContainer}>
        <ClientImageCounts />
        <ClientReceiptCounts />
      </div>

      <ReceiptStack />

      {files.length > 0 && (
        <div
          style={{
            position: "fixed",
            bottom: 0,
            left: 0,
            width: "100%",
            background: "var(--background-color)",
            padding: "1rem",
            boxShadow: "0 -2px 6px rgba(0,0,0,0.2)",
            borderTop: "1px solid var(--text-color)",
            zIndex: 999,
          }}
        >
          <div style={{ maxWidth: "1024px", margin: "0 auto" }}>
            <h4 style={{ margin: "0 0 0.5rem 0" }}>Files to upload:</h4>
            <ul
              style={{
                margin: "0.5rem 0",
                maxHeight: "100px",
                overflowY: "auto",
              }}
            >
              {files.map((f, i) => (
                <li key={i} style={{ fontSize: "0.9rem" }}>
                  {f.name}
                </li>
              ))}
            </ul>
            <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
              <button onClick={uploadToS3} disabled={uploading}>
                {uploading ? "Uploading..." : "Upload to S3"}
              </button>
              {message && (
                <p
                  style={{
                    margin: 0,
                    fontSize: "0.9rem",
                    color: message.includes("successful")
                      ? "var(--color-green)"
                      : "var(--color-var(--color-red))",
                  }}
                >
                  {message}
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
