import { GetStaticProps } from "next";
import Head from "next/head";
import React, { useCallback, useEffect, useState } from "react";
import ClientOnly from "../components/ClientOnly";
import { useQAQueue } from "../hooks/useQAQueue";
import styles from "../styles/Receipt.module.css";

// Import components normally - they'll be wrapped in ClientOnly
import AnimatedInView from "../components/ui/AnimatedInView";
import {
  AddressSimilaritySideBySide,
  AWSFlowDiagram,
  CICDLoop,
  CodeBuildDiagram,
  DynamoStreamAnimation,
  LabelEvaluatorVisualization,
  LabelValidationTimeline,
  LabelWordCloud,
  LayoutLMInferenceVisualization,
  PageCurlLetter,
  PrecisionRecallDartboard,
  QAAgentFlow,
  QuestionMarquee,
  ReceiptBoundingBoxGrid,
  ReceiptStack,
  StreamBitsRoutingDiagram,
  TrainingMetricsAnimation,
  UploadDiagram,
  WordSimilarity,
} from "../components/ui/Figures";
import {
  ChromaLogo,
  ClaudeLogo,
  CursorLogo,
  DockerLogo,
  GithubActionsLogo,
  GithubLogo,
  GoogleMapsLogo,
  LangChainLogo,
  LangSmithLogo,
  PulumiLogo
} from "../components/ui/Logos";
import QueryLabelTransform from "../components/ui/QueryLabelTransform";

interface ReceiptPageProps {
  uploadDiagramChars: string[];
  codeBuildDiagramChars: string[];
}

// Use getStaticProps for static generation - this runs at build time
export const getStaticProps: GetStaticProps<ReceiptPageProps> = async () => {
  // Generate random chars at build time (deterministic for each build)
  // We need 30 bits per stream, and there are up to 8 phases with multiple paths
  // Generate 240 to ensure we have enough for all possible bit streams
  const uploadDiagramChars = Array.from({ length: 120 }, () =>
    Math.random() > 0.5 ? "1" : "0",
  );

  // Generate chars for CodeBuildDiagram (3 phases, similar bit count)
  const codeBuildDiagramChars = Array.from({ length: 120 }, () =>
    Math.random() > 0.5 ? "1" : "0",
  );

  return {
    props: {
      uploadDiagramChars,
      codeBuildDiagramChars,
    },
  };
};

export default function ReceiptPage({
  uploadDiagramChars,
  codeBuildDiagramChars,
}: ReceiptPageProps) {
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

  // --- QA Agent live data ---
  const { data: qaData, questionIndex: selectedQuestion, advance: advanceQuestion, selectQuestion: setSelectedQuestion } = useQAQueue();

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
        <meta property="og:image" content="" />
        <meta name="twitter:card" content="summary" />
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
        I wanted to know exactly how much I spent on milk. This should be easy,
        but receipts are the worst documents ever designed. I taught my laptop
        to read them for me so I don't have to.
      </p>

      <h1>Getting Text Out of the Receipt</h1>

      <p>
        I tried scanning, taking photos, and OCR. None of them worked well. I
        pointed an old OCR engine at a CVS receipt. Here's what I got:
      </p>

      <pre className={styles.codeBlock}>
        <code>{`CVS/pharmacy
1tem              Qy   Pr1ce
M1LK 2%           1    $4.4g`}</code>
      </pre>

      <p>
        I had to get creative. The first principles approach is: a receipt is a
        piece of paper with words on it.
      </p>

      <ClientOnly>
        <PageCurlLetter />
      </ClientOnly>

      <p>
        After determining what a receipt is, I needed to pull the piece of
        paper with words on it out of the image.
      </p>

      <ReceiptBoundingBoxGrid />

      <p>
        This flattened receipt is now as clean as it can be.
      </p>

      <ReceiptStack />

      <h1>Structuring the Chaos</h1>

      <h2>Finding the Store</h2>

      <p>
        Every store formats their receipts differently. Fast food has your
        order number, big and at the top. Target doesn't even have the word
        "Target" at the top??! I needed a way to figure out which store each
        receipt came from.
      </p>

      <ClientOnly>
        <AnimatedInView>
          <GoogleMapsLogo />
        </AnimatedInView>
      </ClientOnly>

      <p>
        First idea: Google Maps. Feed it an address, phone number, website,
        etc. get the store name. This worked! It also cost money. After
        processing 200 receipts I checked my bill: $8. I had 300 left. I needed
        a better way to group the receipts by store without wasting another $8.
      </p>

      <ClientOnly>
        <AnimatedInView>
          <ChromaLogo />
        </AnimatedInView>
      </ClientOnly>

      <p>
        So I added Chroma, a vector database. Great, another database. But
        Chroma let me do something clever: instead of asking Google "what
        store is at 123 Main St," I could ask my own receipts "have I seen
        this address before?"
      </p>

      <ClientOnly>
        <AddressSimilaritySideBySide />
      </ClientOnly>

      <p>
        Turns out receipts from the same store look like receipts from the
        same store. Same address, same phone number, same weird formatting.
        Now I only hit Google for stores I've genuinely never seen. My bill
        dropped to cents.
      </p>


      <h2>Defining the Corpus</h2>

      <p>
        Every receipt has the same kinds of words on it, but every store
        formats it differently. I needed a shared vocabulary.
      </p>

      <ClientOnly>
        <LabelWordCloud />
      </ClientOnly>

      <p>
        The idea: show AI a receipt, ask it to tag each word with a label, then
        compare its answers to other receipts from the same store to see if
        the patterns still hold.
      </p>

      <ClientOnly>
        <LabelEvaluatorVisualization />
      </ClientOnly>

      <p>
        This works, kind of. AI isn't consistent. It would call the price of
        milk the subtotal. It confused "DAIRY" with "MILK." I can't trust
        something that doesn't know what milk is. So I corrected the results
        by asking AI to verify again.
      </p>

      <p>And again. And again.</p>

      <ClientOnly>
        <LabelValidationTimeline />
      </ClientOnly>

      <p>
        Each pass got a little better. The red shrinks, the green grows. But
        asking ~4 different AI, 5+ times to verify the results was slow and
        expensive. I needed a better way.
      </p>

      <h2>Making it Faster and Cheaper</h2>

      <p>
        I needed to stop paying OpenAI to argue with me about what milk is.
        The answer, apparently, was to train my own model.
      </p>

      <p>
        I don't know how to train a model...
      </p>

      <p>
        This is why I needed to make my own AI. I don't know how to make an AI.
        Turns out training an AI involves staring at metrics I didn't fully
        understand. Precision and recall, apparently, are in some way related
        to the accuracy of the model. High precision means "when it says
        'MILK', it's probably right." High recall means "it finds most of the
        MILKs" You can't crank both to 100%.
      </p>

      <PrecisionRecallDartboard />

      <p>
        After a lot of trial and error-tweaking parameters, retraining, staring
        at graphs, I mostly understood what was going on. I found out that this
        is called hyper-parameter tuning.
      </p>

      <ClientOnly>
        <TrainingMetricsAnimation />
      </ClientOnly>

      <ClientOnly>
        <LayoutLMInferenceVisualization />
      </ClientOnly>

      <p>
        The custom model does most of the work. Then I ship the results to AWS
        where a single AI pass confirms or corrects the labels. One call
        instead of 15+.
      </p>

      {/* <ClientOnly>
  <LabelValidationVisualization />
</ClientOnly> */}

      <p>
        Same results. Fraction of the time. Fraction of the cost. I can finally
        afford to find out about the milk.
      </p>

      <ClientOnly>
        <AWSFlowDiagram />
      </ClientOnly>

      <h1>Asking About the $$$ Spent on Milk</h1>

      <p>
        The question still stands: "How much did I spend on milk?"
      </p>

      <p>
        When I ask, the system transforms my question it can actually search
        for:
      </p>


      <ClientOnly>
        <QueryLabelTransform
          query="How much did I spend on milk?"
          transformed='Which receipts have "milk" as PRODUCT_NAME? What is the LINE_TOTAL and/or UNIT_PRICE?'
        />
      </ClientOnly>

      <p>
        It digs through the corpus, finds every mention of milk, and adds
        them up.
      </p>

      <ClientOnly>
        <WordSimilarity />
      </ClientOnly>

      <h1>So Now What?</h1>

      <p>
        Ok, I over-engineered a milk tracker. But here's the thing: now I have
        a system I can actually break.
      </p>

      <p>
        What if I ask it a weird question? What if the receipt is formatted in
        a way I've never seen? What if the AI hallucinates a grocery store that
        doesn't exist? I need to find out.
      </p>

      <ClientOnly>
        <LangChainLogo />
      </ClientOnly>

      <p>
        LangChain lets me wire up the whole pipeline: question in, answer out.
        But more importantly, it lets me throw hundreds of fake questions at
        the system to see what breaks.
      </p>

      <ClientOnly>
        <QAAgentFlow autoPlay={true} questionData={qaData ?? undefined} onCycleComplete={advanceQuestion}>
          <QuestionMarquee rows={4} speed={25} onQuestionClick={setSelectedQuestion} activeQuestion={selectedQuestion} />
        </QAAgentFlow>
      </ClientOnly>

      <p>
        Some work. Some don't. That's the point.
      </p>

      <ClientOnly>
        <LangSmithLogo />
      </ClientOnly>

      <p>
        LangSmith records what happened:  which questions worked, which failed,
        and why. I can use AI to annotate bad answers, use AI to evaluate why
        it went wrong, and plan a new experiment.
      </p>

      <ClientOnly>
        <CICDLoop />
      </ClientOnly>

      <p>
        Change something, ask the questions, check the results, repeat. It's
        less "artificial intelligence" and more "arguing with a very fast
        intern who keeps misreading receipts".
      </p>

      <p>
        Anyway, $800+ on milk last year. I might have a problem.
      </p>

      <hr />

      <h1>The Boring Details</h1>

      <p>
        If you're still here, you might want to know what's actually under the
        hood.
      </p>

      <p>
        I didn't write most of this code by hand. I orchestrated it. Cursor and
        Claude did the typing; I did the thinking. I know I'm cooking when I
        spend more time reviewing changes than writing code.
      </p>

      <ClientOnly>
        <AnimatedInView>
          <div
            style={{
              display: "flex",
              flexDirection: "row",
              justifyContent: "center",
              alignItems: "center",
              gap: "2rem",
              flexWrap: "wrap",
              margin: "2rem 0",
            }}
          >
            <CursorLogo />
            <ClaudeLogo />
          </div>
        </AnimatedInView>
      </ClientOnly>

      <p>
        GitHub Actions let me experiment fast: push a change, watch it break,
        fix it, repeat. Cheap iteration without breaking production.
      </p>

      <ClientOnly>
        <AnimatedInView>
          <div
            style={{
              display: "flex",
              flexDirection: "row",
              justifyContent: "center",
              alignItems: "center",
              gap: "2rem",
              flexWrap: "wrap",
              margin: "2rem 0",
            }}
          >
            <GithubLogo />
            <GithubActionsLogo />
          </div>
        </AnimatedInView>
      </ClientOnly>

      <p>
        I've used Terraform before, but this time I tried Pulumi. It lets me
        define AWS infrastructure in Python, which means I can hack it with
        tools I already know.
      </p>

      <ClientOnly>
        <AnimatedInView>
          <PulumiLogo />
        </AnimatedInView>
      </ClientOnly>

      <p>
        This project has a lot of docker containers. My Pulumi hack bundles and
        ships them to AWS CodeBuild, which builds and deploys them without
        melting my laptop.
      </p>

      <ClientOnly>
        <AnimatedInView>
          <DockerLogo />
        </AnimatedInView>
      </ClientOnly>

      <ClientOnly>
        <CodeBuildDiagram chars={codeBuildDiagramChars} />
      </ClientOnly>

      <p>
        The other thing I leaned into: event-driven architecture. DynamoDB has
        a change data capture stream—whenever something changes, I can react
        to it.
      </p>

      <ClientOnly>
        <DynamoStreamAnimation />
      </ClientOnly>

      <p>
        That's how I keep DynamoDB and Chroma in sync. A change hits Dynamo,
        a Lambda picks it up, Chroma gets updated. No polling, no cron jobs.
      </p>

      <ClientOnly>
        <StreamBitsRoutingDiagram />
      </ClientOnly>

      <p>
        Same pattern for my laptop talking to AWS. SQS queues everywhere.
        Things fail, things retry, nothing gets lost.
      </p>

      <ClientOnly>
        <UploadDiagram chars={uploadDiagramChars} />
      </ClientOnly>

      <p>
        I'll probably keep building this way. It's nice when the system does
        the work.
      </p>

      <p>
        The code is on
        {" "}
        <a href="https://github.com/tnorlund/Portfolio">GitHub</a>{" "}
        if you want to see how the
        sausage gets made. Or the milk, I guess.
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
