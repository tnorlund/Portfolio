import { GetStaticProps } from "next";
import Head from "next/head";
import React, { useCallback, useEffect, useState } from "react";
import { useInView } from "react-intersection-observer";
import ClientOnly from "../components/ClientOnly";
import UploadProgressPanel from "../components/ui/UploadProgressPanel";
import { useQAQueue } from "../hooks/useQAQueue";
import { useUploadProgress } from "../hooks/useUploadProgress";
import styles from "../styles/Receipt.module.css";

// Import components normally - they'll be wrapped in ClientOnly
import AnimatedInView from "../components/ui/AnimatedInView";
import {
  AddressSimilaritySideBySide,
  SynthesisPipeline,
  AWSFlowDiagram,
  CICDLoop,
  CodeBuildDiagram,
  DynamoStreamAnimation,
  LabelValidationTimeline,
  LabelWordCloud,
  LayoutLMInferenceVisualization,
  PageCurlLetter,
  PrecisionRecallDartboard,
  QAAgentFlow,
  QuestionMarquee,
  ReceiptHealthExplorer,
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
  PulumiLogo,
} from "../components/ui/Logos";
import QueryLabelTransform from "../components/ui/QueryLabelTransform";
import { ReceiptFlowLoadingShell } from "../components/ui/Figures/ReceiptFlow/ReceiptFlowLoadingShell";
import TrainingMetricsLoadingShell from "../components/ui/Figures/TrainingMetricsAnimation/TrainingMetricsLoadingShell";
import WordSimilarityLoadingShell from "../components/ui/Figures/WordSimilarityLoadingShell";

interface ReceiptPageProps {
  uploadDiagramChars: string[];
  codeBuildDiagramChars: string[];
}

interface FigureBoundaryProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
  name: string;
  intrinsicSize: string;
  mobileIntrinsicSize?: string;
}

const FIGURE_LAZY_ROOT_MARGIN = "1000px 0px";

const LayoutLMLoadingFallback = () => (
  <div
    style={{
      width: "100%",
      maxWidth: "1000px",
      margin: "2rem auto",
      padding: "1.5rem",
      background: "var(--background-color)",
      borderRadius: "12px",
    }}
  >
    <ReceiptFlowLoadingShell
      variant="layoutlm"
      layoutVars={{ "--rf-align-items": "center" } as React.CSSProperties}
    />
  </div>
);

const FigureBoundary = ({
  children,
  fallback = null,
  name,
  intrinsicSize,
  mobileIntrinsicSize = intrinsicSize,
}: FigureBoundaryProps) => {
  const { ref, inView } = useInView({
    rootMargin: FIGURE_LAZY_ROOT_MARGIN,
    triggerOnce: true,
    fallbackInView: true,
  });
  const [shouldRender, setShouldRender] = useState(false);

  useEffect(() => {
    if (inView) {
      setShouldRender(true);
    }
  }, [inView]);

  return (
    <div
      ref={ref}
      className={styles.figureBoundary}
      data-figure-boundary={name}
      data-lazy-pending={shouldRender ? undefined : "true"}
      style={
        {
          "--figure-intrinsic-size": intrinsicSize,
          "--figure-mobile-intrinsic-size": mobileIntrinsicSize,
        } as React.CSSProperties
      }
    >
      {shouldRender ? children : fallback}
    </div>
  );
};

const QAAgentFigure = () => {
  const {
    data: qaData,
    questionIndex: selectedQuestion,
    advance: advanceQuestion,
    selectQuestion: setSelectedQuestion,
  } = useQAQueue();

  if (!qaData) {
    return <ReceiptFlowLoadingShell variant="qa" />;
  }

  return (
    <QAAgentFlow autoPlay={true} questionData={qaData} onCycleComplete={advanceQuestion}>
      <QuestionMarquee rows={4} speed={25} onQuestionClick={setSelectedQuestion} activeQuestion={selectedQuestion} />
    </QAAgentFlow>
  );
};

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

  // --- Receipt Upload State & Handlers ---
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

  const { fileStates, addFiles, clearAll } = useUploadProgress(apiUrl);

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const dt = e.dataTransfer;
      if (dt && dt.files && dt.files.length > 0) {
        addFiles(Array.from(dt.files));
      }
    },
    [addFiles],
  );

  const handleDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragging(false);
  }, []);

  const handleFileInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0) {
        addFiles(Array.from(e.target.files));
      }
    },
    [addFiles],
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

      <FigureBoundary name="page-curl" intrinsicSize="160px">
        <ClientOnly>
          <PageCurlLetter />
        </ClientOnly>
      </FigureBoundary>

      <p>
        After determining what a receipt is, I needed to pull the piece of
        paper with words on it out of the image.
      </p>

      <FigureBoundary
        name="receipt-bounding-box-grid"
        intrinsicSize="960px"
        mobileIntrinsicSize="440px"
      >
        <ReceiptBoundingBoxGrid />
      </FigureBoundary>

      <p>
        This flattened receipt is now as clean as it can be.
      </p>

      <FigureBoundary name="receipt-stack" intrinsicSize="800px">
        <ReceiptStack />
      </FigureBoundary>

      <h1>Structuring the Chaos</h1>

      <h2>Finding the Store</h2>

      <p>
        Every store formats their receipts differently. Fast food has your
        order number, big and at the top. Target doesn't even have the word
        "Target" at the top??! I needed a way to figure out which store each
        receipt came from.
      </p>

      <FigureBoundary name="google-maps-logo" intrinsicSize="150px">
        <ClientOnly>
          <AnimatedInView>
            <GoogleMapsLogo />
          </AnimatedInView>
        </ClientOnly>
      </FigureBoundary>

      <p>
        First idea: Google Maps. Feed it an address, phone number, website,
        etc. get the store name. This worked! It also cost money. After
        processing 200 receipts I checked my bill: $8. I had 300 left. I needed
        a better way to group the receipts by store without wasting another $8.
      </p>

      <FigureBoundary name="chroma-logo" intrinsicSize="150px">
        <ClientOnly>
          <AnimatedInView>
            <ChromaLogo />
          </AnimatedInView>
        </ClientOnly>
      </FigureBoundary>

      <p>
        So I added Chroma, a vector database. Great, another database. But
        Chroma let me do something clever: instead of asking Google "what
        store is at 123 Main St," I could ask my own receipts "have I seen
        this address before?"
      </p>

      <FigureBoundary
        name="address-similarity"
        intrinsicSize="460px"
        mobileIntrinsicSize="360px"
      >
        <ClientOnly>
          <AddressSimilaritySideBySide />
        </ClientOnly>
      </FigureBoundary>

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

      <FigureBoundary
        name="label-word-cloud"
        intrinsicSize="580px"
        mobileIntrinsicSize="400px"
      >
        <ClientOnly>
          <LabelWordCloud />
        </ClientOnly>
      </FigureBoundary>

      <p>
        The idea: show AI a receipt, ask it to tag each word with a label.
        Then cross-check those labels against what we already know. The health
        check looks at the merchant identity, the receipt-specific formats,
        and the arithmetic on the totals.
      </p>

      <FigureBoundary name="receipt-health" intrinsicSize="660px">
        <ClientOnly>
          <ReceiptHealthExplorer />
        </ClientOnly>
      </FigureBoundary>

      <p>
        Even with those checks, the labels weren't perfect. So I corrected the
        results by asking AI to verify them. And again. And again.
      </p>

      <FigureBoundary
        name="label-validation"
        intrinsicSize="900px"
        mobileIntrinsicSize="900px"
      >
        <ClientOnly>
          <LabelValidationTimeline />
        </ClientOnly>
      </FigureBoundary>

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

      <FigureBoundary name="precision-recall" intrinsicSize="440px">
        <PrecisionRecallDartboard />
      </FigureBoundary>

      <p>
        After a lot of trial and error-tweaking parameters, retraining, staring
        at graphs, I mostly understood what was going on. I found out that this
        is called hyper-parameter tuning.
      </p>

      <FigureBoundary
        name="training-metrics"
        intrinsicSize="640px"
        mobileIntrinsicSize="600px"
        fallback={<TrainingMetricsLoadingShell />}
      >
        <ClientOnly fallback={<TrainingMetricsLoadingShell />}>
          <TrainingMetricsAnimation />
        </ClientOnly>
      </FigureBoundary>

      <FigureBoundary
        name="layoutlm-inference"
        intrinsicSize="640px"
        fallback={<LayoutLMLoadingFallback />}
      >
        <ClientOnly fallback={<LayoutLMLoadingFallback />}>
          <LayoutLMInferenceVisualization />
        </ClientOnly>
      </FigureBoundary>

      <p>
        More training data helps, but only if it&apos;s the right kind. If the
        model keeps missing discounts, weird quantities, or item names that run
        forever, I can make receipts full of that exact nonsense. I start with
        a real labeled receipt, swap items in and out using that store&apos;s own
        patterns, fix the totals, and render it again. Same receipt DNA. New
        failure case. Free labels.
      </p>

      <FigureBoundary name="synthesis-pipeline" intrinsicSize="640px">
        <ClientOnly>
          <SynthesisPipeline />
        </ClientOnly>
      </FigureBoundary>

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

      <FigureBoundary name="aws-flow" intrinsicSize="240px">
        <ClientOnly>
          <AWSFlowDiagram />
        </ClientOnly>
      </FigureBoundary>

      <h1>Asking About the $$$ Spent on Milk</h1>

      <p>
        The question still stands: "How much did I spend on milk?"
      </p>

      <p>
        When I ask, the system transforms my question it can actually search
        for:
      </p>


      <FigureBoundary
        name="query-transform"
        intrinsicSize="160px"
        mobileIntrinsicSize="120px"
      >
        <ClientOnly>
          <QueryLabelTransform
            query="How much did I spend on milk?"
            transformed='Which receipts have "milk" as PRODUCT_NAME? What is the LINE_TOTAL and/or UNIT_PRICE?'
          />
        </ClientOnly>
      </FigureBoundary>

      <p>
        It digs through the corpus, finds every mention of milk, and adds
        them up.
      </p>

      <FigureBoundary
        name="word-similarity"
        intrinsicSize="1160px"
        mobileIntrinsicSize="1160px"
        fallback={<WordSimilarityLoadingShell />}
      >
        <ClientOnly fallback={<WordSimilarityLoadingShell />}>
          <WordSimilarity />
        </ClientOnly>
      </FigureBoundary>

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

      <FigureBoundary name="langchain-logo" intrinsicSize="150px">
        <ClientOnly>
          <AnimatedInView>
            <LangChainLogo />
          </AnimatedInView>
        </ClientOnly>
      </FigureBoundary>

      <p>
        LangChain lets me wire up the whole pipeline: question in, answer out.
        But more importantly, it lets me throw hundreds of fake questions at
        the system to see what breaks.
      </p>

      <FigureBoundary
        name="qa-agent"
        intrinsicSize="1040px"
        mobileIntrinsicSize="1280px"
      >
        <ClientOnly>
          <QAAgentFigure />
        </ClientOnly>
      </FigureBoundary>

      <p>
        Some work. Some don't. That's the point.
      </p>

      <FigureBoundary name="langsmith-logo" intrinsicSize="150px">
        <ClientOnly>
          <AnimatedInView>
            <LangSmithLogo />
          </AnimatedInView>
        </ClientOnly>
      </FigureBoundary>

      <p>
        LangSmith records what happened: which questions worked, which failed,
        and why. I can use AI to annotate bad answers, use AI to evaluate why
        it went wrong, and plan a new experiment.
      </p>

      <FigureBoundary name="cicd-loop" intrinsicSize="320px">
        <ClientOnly>
          <CICDLoop />
        </ClientOnly>
      </FigureBoundary>

      <p>
        Change something, ask the questions, check the results, repeat. It's
        less "artificial intelligence" and more "arguing with a very fast
        intern who keeps misreading receipts".
      </p>

      <p>
        Anyway, $900+ on milk last year. I might have a problem.
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

      <FigureBoundary
        name="cursor-claude-logos"
        intrinsicSize="220px"
        mobileIntrinsicSize="400px"
      >
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
      </FigureBoundary>

      <p>
        GitHub Actions let me experiment fast: push a change, watch it break,
        fix it, repeat. Cheap iteration without breaking production.
      </p>

      <FigureBoundary
        name="github-logos"
        intrinsicSize="220px"
        mobileIntrinsicSize="400px"
      >
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
      </FigureBoundary>

      <p>
        I've used Terraform before, but this time I tried Pulumi. It lets me
        define AWS infrastructure in Python, which means I can hack it with
        tools I already know.
      </p>

      <FigureBoundary name="pulumi-logo" intrinsicSize="150px">
        <ClientOnly>
          <AnimatedInView>
            <PulumiLogo />
          </AnimatedInView>
        </ClientOnly>
      </FigureBoundary>

      <p>
        This project has a lot of docker containers. My Pulumi hack bundles and
        ships them to AWS CodeBuild, which builds and deploys them without
        melting my laptop.
      </p>

      <FigureBoundary name="docker-logo" intrinsicSize="150px">
        <ClientOnly>
          <AnimatedInView>
            <DockerLogo />
          </AnimatedInView>
        </ClientOnly>
      </FigureBoundary>

      <FigureBoundary
        name="codebuild-diagram"
        intrinsicSize="260px"
        mobileIntrinsicSize="460px"
      >
        <ClientOnly>
          <CodeBuildDiagram chars={codeBuildDiagramChars} />
        </ClientOnly>
      </FigureBoundary>

      <p>
        The other thing I leaned into: event-driven architecture. DynamoDB has
        a change data capture stream—whenever something changes, I can react
        to it.
      </p>

      <FigureBoundary name="dynamo-stream" intrinsicSize="160px">
        <ClientOnly>
          <DynamoStreamAnimation />
        </ClientOnly>
      </FigureBoundary>

      <p>
        That's how I keep DynamoDB and Chroma in sync. A change hits Dynamo,
        a Lambda picks it up, Chroma gets updated. No polling, no cron jobs.
      </p>

      <FigureBoundary name="stream-bits-routing" intrinsicSize="360px">
        <ClientOnly>
          <StreamBitsRoutingDiagram />
        </ClientOnly>
      </FigureBoundary>

      <p>
        Same pattern for my laptop talking to AWS. SQS queues everywhere.
        Things fail, things retry, nothing gets lost.
      </p>

      <FigureBoundary name="upload-diagram" intrinsicSize="360px">
        <ClientOnly>
          <UploadDiagram chars={uploadDiagramChars} />
        </ClientOnly>
      </FigureBoundary>

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

      <UploadProgressPanel fileStates={fileStates} onClear={clearAll} />
    </div>
  );
}
