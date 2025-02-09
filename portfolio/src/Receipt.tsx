// import ReceiptGrid from "./ReceiptGrid";
import Diagram from "./Diagram";
import Pulumi from "./Pulumi";
import OpenAI from "./OpenAI";
import ReceiptStack from "./ReceiptStack";
import ReceiptWords from "./ReceiptWords";
import ImageBoundingBox from "./ImageBoundingBox";
import TypeScriptLogo from "./TypeScriptLogo";
import ReactLogo from "./ReactLogo";
import { ReceiptCounts, ImageCounts } from "./DataCounts";
import TagValidationChart from "./components/TagValidationChart";
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

      <h1>ChatGPT</h1>

      <h2>Automated Labeling</h2>
      <p>
        One major time-saver in this project was using ChatGPT's API to tag the
        words on each receipt automatically. Working in the Cursor IDE, I
        iterated quickly on the code and prompts—adjusting JSON formats and how
        word locations are highlighted—until the model produced consistent and
        accurate labels.
      </p>

      <TagValidationChart />

      <h2>Two-Step Validation</h2>
      <p>
        Although ChatGPT sped up labeling, I added a second layer of checks to
        ensure accuracy. After ChatGPT labels the words, I run a follow-up
        pass—partly automated and partly manual—to confirm correctness. This
        approach provides reliable results without losing the time-saving
        benefits. By combining ChatGPT, prompt engineering, and the Cursor IDE,
        I built maintainable code at a faster pace than before.
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
        I still have to verify the word tags to catch mistakes. For instance,
        phone numbers shouldn't have letters. My plan is to further refine the
        tag validation by looking at the order of characters, not just their
        types. That way, I can automate more of the process without depending on
        ChatGPT. Eventually, I hope to create a flexible, hardware-agnostic
        model that scales smoothly.
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
