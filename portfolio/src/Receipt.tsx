// import ReceiptGrid from "./ReceiptGrid";
import Diagram from "./Diagram";
import Pulumi from "./Pulumi";
import OpenAI from "./OpenAI";
import ReceiptStack from "./ReceiptStack";
import ReceiptWords from "./ReceiptWords";
import ImageBoundingBox from "./ImageBoundingBox";
import TypeScriptLogo from "./TypeScriptLogo";
import ReactLogo from "./ReactLogo";
import Pinecone from "./Pinecone";
import GooglePlaces from "./GooglePlaces";
import EmbeddingDiagram from "./embedding_diagram";
import { ReceiptCounts, ImageCounts } from "./DataCounts";
import LabelValidationChart from "./LabelValidationCount";
import HuggingFace from "./HuggingFace";
import "./Receipt.css";

function Receipt() {
  return (
    <div>
      <h1>Introduction</h1>
      <h2>The Inspiration</h2>
      <p>
        In middle school, I spent a summer sorting my dad's receipts for his
        taxes. It was tedious work, but I saved enough for a few Xbox games.
        Back then, automation wasn't an option. Today, thanks to a ChatGPT Pro
        subscription, my programming skills have grown rapidly. Now I'm
        automating not just the organizing of receipts, but also parts of my own
        coding process.
      </p>

      <h1>Strategy</h1>
      <h2>Bridging Skills With New Tools</h2>
      <p>
        I came into this project comfortable with AWS and a bit of React, but
        Swift was new territory. ChatGPT's advanced “reasoning” mode helped me
        lay out a plan that linked my familiar skill set to fresh challenges
        like integrating Optical Character Recognition (OCR). This head start
        let me quickly put together the project's core components.
      </p>
      <h2>Constant Iteration</h2>
      <p>
        My initial plan gave me a decent roadmap, but the real work required
        continuous updates. I used feedback and rigorous testing at every step.
        My background in AWS system design, React, and QA testing helped me spot
        and fix issues early on, keeping the project on track.
      </p>

      <h1>OCR and Image Processing</h1>

      <h2>Choosing the Right OCR</h2>
      <p>
        I first tried Tesseract for OCR, but it wasn't capturing the details I
        needed. Switching to Apple's Vision OCR gave me better data, including
        bounding boxes and confidence scores. From there, I grouped and
        standardized the OCR results so every receipt would be labeled
        consistently.
      </p>
      <h2>Clustering Receipts</h2>
      <p>
        To figure out which words belonged on the same receipt, I used a method
        called DBSCAN clustering. It groups nearby text elements along the
        X-axis, handy for tall receipts. After grouping, I normalized all OCR
        coordinates to fit inside a standard 1x1 square, making the data easier
        to handle later on.
      </p>
      <ImageBoundingBox />

      <h1>Infrastructure</h1>
      <h2>Pulumi and AWS</h2>
      <p>
        I was familiar with AWS and Terraform, but Pulumi was new. ChatGPT
        helped me quickly set up consistent cloud services for both
        “development” and “production” environments, so I could test changes
        without risk and optimize my cloud usage.
      </p>
      <div className="logos-container">
        <Pulumi />
        <OpenAI />
      </div>
      <h2>AWS Services</h2>
      <p>
        These AWS services form the backbone of the backend, ensuring fast image
        processing, secure storage, and reliable data management.
      </p>
      <ul style={{ marginTop: "1rem" }}>
        <li>
          <strong>AWS Lambda</strong>: Manages image processing and workflow
          orchestration.
        </li>
        <li>
          <strong>AWS S3</strong>: Stores receipt images securely.
        </li>
        <li>
          <strong>AWS DynamoDB</strong>: Maintains receipt metadata with quick
          read/write capabilities.
        </li>
        <li>
          <strong>AWS API Gateway</strong>: Exposes the backend API to client
          requests.
        </li>
        <li>
          <strong>AWS CloudFront</strong>: Delivers content quickly and securely
          as a CDN.
        </li>
      </ul>
      <Diagram />

      <h1>Semantic Labeling</h1>

      <p>
        I used ChatGPT to automate the assigning and validation of labels for
        receipt text using a combination of OpenAI's embedding API, batch
        completion API, and aa multipass validation workflow. This pipeline
        minimizes costs, maximizes label accuracy, and reduces the need for
        manual review.
      </p>
      <h2>Finding the Merchant</h2>
      <p>
        After using Apple's OCR to extract words, I used a combination of the
        Google Places API and OpenAI's Agents SDK to find the merchant behind
        the receipt. The is allows me to add more context to the labelling
        process and reduce the number of false positives.
      </p>
      <GooglePlaces />

      <h2>Embedding and Retrieval</h2>
      <p>
        I used OpenAI's embedding API to convert receipt words into
        high-dimensional vectors that capture semantic meaning. I used the batch
        API to embed large chunks of words at once, which significantly reduced
        cost and request overhead. These embeddings are stored in Pinecone, a
        vector database.
      </p>
      <Pinecone />
      <p>
        Vector databases are a type of database that store data in a
        high-dimensional space. They are used to store embeddings and retrieve
        similar items. This allows me to retrieve similar words and see how
        they're used.
      </p>
      <EmbeddingDiagram />

      <h2>Batch Validation with OpenAI</h2>

      <p>
        After labels were applied, I used OpenAI's batch API to validate them.
        The batch process is done in three passes:
      </p>
      <h3>First Pass</h3>
      <p>
        The first pass is a simple validation of the labels. Given the word and
        the proposed label, the model will either approve or reject the label.
        If rejected, it provides a recommendation for the correct label.
      </p>
      <h3>Second Pass</h3>
      <p>
        The second pass uses the list of invalid labels from the first pass to
        retrieve examples of the correct label. It then uses the retrieved
        examples to generate a new label for the word.
      </p>
      <h3>Third Pass</h3>
      <p>
        The third pass compares similar words and their labels. After providing
        valid and invalid examples, ChatGPT either validates that the label is a
        part of the corpus or that it needs manual review.
      </p>
      <LabelValidationChart />
      <h2>The Feedback Loop</h2>
      <p>
        This process is repeated to continuously improve the accuracy of the
        labels. I'm able to copy all labels that require manual review to ask
        ChatGPT for common errors. As part of this loop, I use{" "}
        <strong>RAGAS</strong> to automatically evaluate how faithful and
        relevant the model’s responses are to the retrieved context. This gives
        me a structured way to identify blind spots and quantify improvements as
        I embed newly validated examples back into the retrieval system.
      </p>
      <h1>Training My Own Model</h1>
      <p>
        To push the limits of automated receipt understanding, I've been
        experimenting with transformer-based models available on{" "}
        <strong>Hugging Face</strong>, a platform that hosts a vast library of
        pre-trained models for tasks like token classification, document layout
        understanding, and more. The most promising model I've found is
        <strong>LayoutLM</strong>.
      </p>
      <HuggingFace />
      <p>
        LayoutLM is specifically designed for visually rich documents like
        receipts and invoices. It uses the text and how it relates to the text
        around it to make predictions.
      </p>
      <p>
        I ended up writing some infrastructure to spin up spot instances on AWS
        to train the model. I was able to train a model at 10% the cost, but my
        dataset isn't large enough to get good results. I'll update my results
        here as I continue to work on this project.
      </p>

      <h1>Frontend</h1>
      <h2>TypeScript + React</h2>
      <p>
        I chose React with TypeScript for the frontend. TypeScript's type system
        gave ChatGPT helpful context about the data, making it easier to handle
        the code. As the backend and infrastructure evolved, I kept the frontend
        in sync, ensuring smooth, up-to-date functionality.
      </p>
      <div className="logos-container">
        <ReactLogo />
        <TypeScriptLogo />
      </div>
      <h2>Continuous Testing</h2>
      <p>
        A continuous integration and delivery (CICD) pipeline allowed me to
        apply and test every improvement right away. This kept the frontend
        aligned with the latest changes, preventing any major issues from
        slipping through.
      </p>

      <h1>Conclusion</h1>
      <h2>Lessons Learned</h2>
      <p>
        From picking the right OCR tools to refining ChatGPT prompts and fixing
        early issues like duplicate uploads, this project was an exercise in
        constant improvement. In the end, I built a solid system for
        automatically extracting data from receipts. The same tagging approach
        could work for many other types of data or media, offering broader
        possibilities for cleaning and training.
      </p>
      <h2>Validation and Future Plans</h2>
      <p>
        I am currently labeling the outliers manually to catch any inaccuracies.
        At the same time, I'm developing a custom classifier to replace both
        ChatGPT's automated labeling and Apple's OCR system. By reducing
        reliance on external tools, this approach will streamline the validation
        process and deliver faster, more reliable performance across any
        platform.
      </p>
      <h2>A New Era of Coding</h2>
      <p>
        Artificial intelligence is advancing quickly and changing how we write
        software. However, no matter how smart computers become, we still need
        solid engineering practices, clever problem-solving, and expert
        knowledge. This project showed that ChatGPT is not just a
        spellchecker—it's a helpful partner that brings new ideas to software
        development. I'm excited to see a future where people and AI work
        together to make programming faster, smarter, and easier for everyone.
      </p>
      <div className="logos-container">
        <ImageCounts />
        <ReceiptCounts />
      </div>

      <ReceiptStack />
      <ReceiptWords />
    </div>
  );
}

export default Receipt;
