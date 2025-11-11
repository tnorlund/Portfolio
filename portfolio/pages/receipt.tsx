import { GetStaticProps } from "next";
import Head from "next/head";
import React, { useCallback, useEffect, useState } from "react";
import ClientOnly from "../components/ClientOnly";
import styles from "../styles/Receipt.module.css";

// Import components normally - they'll be wrapped in ClientOnly
import AnimatedInView from "../components/ui/AnimatedInView";
import {
  AddressSimilaritySideBySide,
  ClientImageCounts,
  ClientReceiptCounts,
  ImageStack,
  LabelValidationCount,
  MerchantCount,
  PhotoReceiptBoundingBox,
  RandomReceiptWithLabels,
  ReceiptStack,
  ScanReceiptBoundingBox,
  UploadDiagram,
  ZDepthConstrained,
  ZDepthUnconstrained,
} from "../components/ui/Figures";
import {
  ChromaLogo,
  GithubActionsLogo,
  GithubLogo,
  GoogleMapsLogo,
  HuggingFaceLogo,
  PulumiLogo,
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
    Math.random() > 0.5 ? "1" : "0",
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
        : "https://upload.tylernorlund.com",
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
              `Failed to request upload URL for ${file.name} (status ${presignRes.status})`,
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
          `Upload successful: ${selectedFiles.map((f) => f.name).join(", ")}`,
        );
        setFiles([]);
      } catch (err) {
        console.error(err);
        setMessage("Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [apiUrl],
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
    [uploadToS3Internal],
  );

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragging(false);
  }, []);

  const uploadToS3 = useCallback(() => {
    // Use functional update to get current files without adding to dependencies
    setFiles((currentFiles) => {
      uploadToS3Internal(currentFiles);
      return []; // Clear files after initiating upload
    });
  }, [uploadToS3Internal]);

  const handleFileInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) {
        const newFiles = Array.from(e.target.files);
        setFiles((prev) => [...prev, ...newFiles]);
        uploadToS3Internal(newFiles);
      }
    },
    [uploadToS3Internal],
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
        paper into structured data, you have to read three layers of data:
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

      {/*
      TODO: Fix OCR Section Disconnect - Show Visual Mess Before Technical Solution

      PROBLEM IDENTIFIED:
      - Text jumps straight into technical solutions ("DBSCAN clustering, convex hull")
      - Visuals show perfect, clean geometric processing
      - Missing the messy visual reality that creates the problem
      - No clear "why" for non-technical readers about why this clustering matters

      SOLUTION STRATEGY:
      Create a new flow that shows the mess first, then the solution:

      1. START WITH CHAOS: Show ReceiptStack (or similar) to demonstrate real-world receipt images:
         - Crumpled and wrinkled receipts
         - Photos taken at angles
         - Multiple receipts overlapping in frame
         - Poor lighting conditions
         - Mixed with other objects/backgrounds

      2. EXPLAIN THE CONSEQUENCE: Without proper processing, text recognition gives:
         - Jumbled words with no grouping
         - Text from multiple receipts mixed together
         - Wrong reading order
         - Background noise included as "receipt data"

      3. PRESENT SIMPLE SOLUTION: "The challenge isn't just reading text - it's figuring
         out which words belong together"

      4. SHOW TECHNICAL SOLUTION WORKING: Current ScanReceiptBoundingBox and
         PhotoReceiptBoundingBox visualizations now make sense as the "after"

      IMPLEMENTATION NEEDED:
      - Move ReceiptStack or create similar component showing messy input images
      - Rewrite intro text to be accessible (avoid OCR jargon)
      - Focus on "grouping text that belongs together" rather than geometric algorithms
      - Position existing visualizations as the successful "clean" result

      This makes "cleanest, most organized approach" meaningful because readers see
      the chaotic starting point first.
      */}

      <h1>From Images to Words and Coordinates</h1>

      <p>
        Every receipt&apos;s journey begins as raw pixels, whether a crisp scan
        or a hastily snapped photo. But hidden within those pixels is structured
        information waiting to be extracted.
      </p>

      <ImageStack />

      <p>
        Text recognition finds words, but not context. Multiple receipts become
        one confusing jumble. The critical step: determining{" "}
        <strong>which words belong together</strong>.
      </p>

      <h2>Scans vs Photos</h2>
      <p>
        This is where the difference between <strong>scanned receipts</strong>{" "}
        and <strong>photographed receipts</strong> becomes crucial. Scans are
        the easy case: flat, well-lit, and aligned. But photos? They&apos;re
        captured at odd angles, under harsh store lighting, with shadows and
        curves that confuse even the best text recognition. Each type needs its
        own approach to group related words and separate one receipt from
        another.
      </p>

      <h2>Scans</h2>

      <p>
        With scans being the easier of the two, we can say that the receipt is
        parallel to the image sensor.
      </p>

      <ClientOnly>
        <ZDepthConstrained />
      </ClientOnly>

      <p>
        Grouping the scanned words is straightforward. Since the scanner
        captures everything flat and aligned, we can use simple rules: words
        that line up horizontally likely belong to the same line item, and
        everything on the page belongs to the same receipt. It&apos;s like
        reading a well-organized spreadsheet.
      </p>

      <ScanReceiptBoundingBox />
      <h2>Photos</h2>
      <p>
        Photos are trickier. When you snap a picture of a receipt on a counter,
        the camera captures it from an angle. Words at the top might appear
        smaller than words at the bottom. Lines that should be straight look
        curved. And if there are multiple receipts in frame? Now you need to
        figure out which words belong to which piece of paper.
      </p>

      <ClientOnly>
        <ZDepthUnconstrained />
      </ClientOnly>

      <p>
        To solve this puzzle, I look for clusters of words that seem to move
        together—like finding constellations in a sky full of stars. Words that
        are close together and follow similar patterns likely belong to the same
        receipt. Once identified, I can digitally &quot;flatten&quot; each
        receipt, making it as clean as a scan.
      </p>

      <PhotoReceiptBoundingBox />

      <h2>Making It Work at Scale</h2>
      <p>
        Now that we can group words correctly, we face a new challenge:
        processing thousands of receipts efficiently. My solution? My laptop
        handles the tricky word orientation while the cloud handles the visual
        processing (finding and flattening receipts).
      </p>

      <ClientOnly>
        <UploadDiagram chars={uploadDiagramChars} />
      </ClientOnly>

      <p>
        This hybrid approach has processed hundreds of receipts, transforming
        messy photos and scans into organized, searchable data:
      </p>

      <ReceiptStack />

      <div
        style={{
          display: "flex",
          justifyContent: "space-around",
          marginTop: "3rem",
          marginBottom: "3rem",
          flexWrap: "wrap",
          gap: "2rem",
        }}
      >
        <ClientImageCounts />
        <ClientReceiptCounts />
      </div>

      <h2>Identifying the Merchant</h2>
      <p>
        Knowing which business a receipt comes from allows for faster
        processing. I wrote an agent that uses Google Maps to identify the
        business the receipt came from. The AI agent is able to consider OCR
        errors, "Mestlake" instead of "Westlake", and still identify the correct
        business.
      </p>

      <ClientOnly>
        <AnimatedInView>
          <GoogleMapsLogo />
        </AnimatedInView>
      </ClientOnly>

      <p>
        The agent tries multiple strategies to identify the business. I give it
        the tools to search by phone, address, and text. When one approach
        fails, it combines the other approaches to identify the business.
      </p>

      <MerchantCount />

      <h2>Finding Similar Receipts</h2>

      <p>
        Once I've identified the business, I can reuse that information for
        similar receipts. I use Chroma to find similar receipts by comparing
        addresses, phone numbers, and URLs.
      </p>

      <ClientOnly>
        <AnimatedInView>
          <ChromaLogo />
        </AnimatedInView>
      </ClientOnly>

      <p>
        Chroma stores text as vector embeddings: a numerical representation of
        the text. Describing text as numbers allows for easy comparison in
        larger datasets. When I search for '1012 Westlake Blvd', it finds
        similar addresses even if the wording is slightly different.
      </p>

      <ClientOnly>
        <AddressSimilaritySideBySide />
      </ClientOnly>

      <p>
        When Chroma finds another receipt with the same address, phone number,
        or URL, I can skip Google Maps and reuse the information from the
        previous receipt, making this process faster and cheaper.
      </p>

      <h2>Labeling the Receipts</h2>

      <p>
        After finding the business and similar receipts, I can use this context
        to label the words more accurately. Each label structures the data for
        processing.
      </p>

      <ClientOnly>
        <RandomReceiptWithLabels />
      </ClientOnly>

      <p>
        The agent verifies the label guesses using a chain of verification. It
        generates a series of questions to answer, and uses the answers to
        verify the label. If the label is incorrect, the agent generates a new
        label and verifies it again.
      </p>

      <LabelValidationCount />

      <p>
        This dataset is used to train a model to label the words faster and
        cheaper. I found a model, LayoutLM, on Hugging Face that can label the
        words given the OCR data.
      </p>

      <ClientOnly>
        <AnimatedInView>
          <HuggingFaceLogo />
        </AnimatedInView>
      </ClientOnly>

      <p>
        Training the model to produce the best results means finding the right
        settings. Instead of trying every possible setting, I use an LLM to
        review training results and suggest which settings to try next. It
        learns what works and what doesn't, helping me find better
        configurations faster.
      </p>

      <h1>Conclusion</h1>

      <p>
        AI has made <i>typing</i> cheap, but the bottlenecks remain{" "}
        <i>understanding</i>, <i>testing</i>, <i>reviewing</i>, and{" "}
        <i>trusting</i>. By shifting my focus from typing code to testing,
        understanding, and architecting solutions, I prototype and experiment
        faster. I can talk to AI, try a new approach, and see my changes in the
        cloud. With a 10-second feedback loop and tests gating production
        deploys, I meet the no-downtime requirement while iterating quickly. I
        no longer spend most of my time researching how to accomplish the task,
        I just build it.
      </p>

      <p>
        I&apos;ve been able to iterate quickly using Pulumi&apos;s
        Infrastructure as Code (IaC).
      </p>

      <ClientOnly>
        <AnimatedInView>
          <PulumiLogo />
        </AnimatedInView>
      </ClientOnly>

      <p>
        This has allowed me to try something new <i>at scale</i> quickly. These
        cheap tries are allowing me to &ldquo;skill-max&ldquo; my cloud
        expertise.
      </p>

      <p>
        This rapid iteration with developer best practices allows me to
        prototype in a safe environment, review and test my changes, and ship
        with confidence using GitHub and Pulumi.
      </p>

      <div className={styles.logosContainer}>
        <ClientOnly>
          <AnimatedInView>
            <GithubLogo />
          </AnimatedInView>
        </ClientOnly>
        <ClientOnly>
          <AnimatedInView>
            <GithubActionsLogo />
          </AnimatedInView>
        </ClientOnly>
      </div>

      <p>
        Understanding how software works, testing to make sure it works, and
        trusting that changes don&apos;t break things still takes a long time.
        Prototyping and shipping to cloud to test new features in seconds speeds
        up development, makes learning fun, and allows me to focus on trying
        something new without being afraid of breaking things. I now optimize
        development loop speed, not keystrokes.
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
                      : "var(--color-red)",
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
