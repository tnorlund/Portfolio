// import ReceiptGrid from "./ReceiptGrid";
import Diagram from "./Diagram";
import Pulumi from "./Pulumi";
import OpenAI from "./OpenAI";
import ReceiptStack from "./ReceiptStack";
import ReceiptWords from "./ReceiptWords";
import ImageBoundingBox from "./ImageBoundingBox";
import TypeScriptLogo from "./TypeScriptLogo";
import ReactLogo from "./ReactLogo";

import "./Receipt.css";

function GPTPrompt() {
  const prompt = `You are a helpful assistant that extracts structured data from a receipt.

Below is a sample of the JSON you will receive. Notice that each 'word' has a 'text', a 'centroid' [x, y], and a line/word ID (l, w):

\`\`\`json
{  
  "receipt": {
    "receipt_id": 123
    // more receipt-level fields
  },
  "w": [
    {
      "t": "BANANAS",
      "c": [0.1234, 0.5678],
      "l": 0,
      "w": 0
    },
    {
      "t": "1.99",
      "c": [0.2345, 0.6789],
      "l": 0,
      "w": 1
    }
    // ...
  ]
}
\`\`\`
The receipt's OCR text is:

. . .

Your task:
   Identify the following fields in the text:
   - store_name (string)
   - date (string)
   - time (string)
   - phone_number (string)
   - total_amount (number)
   - taxes (number)
   - address (string)
   - For line items, return three separate fields:
       * line_item: includes all words that contribute to any line item
       * line_item_name: includes words that contribute to the item name
       * line_item_price: includes words that contribute to the item price

   Instead of returning the text or centroid, **return an array of {"l": <line_id>, "w": <word_id>} for each field.**
   - For example, if you think the first two words (line_id=0, word_id=0 and line_id=0, word_id=1) make up 'store_name', return:
     "store_name": [
       {"l": 0, "w": 0},
       {"l": 0, "w": 1}
     ]
   - If you cannot find a particular field, return an empty array for it.

**Output Requirements**:
 - Output must be valid JSON.
 - Do not return additional keys or text.
 - Do not invent new {"l", "w"} pairs. Only use those provided in the 'words' list.
 - If none found, return empty arrays.

Example output:
\`\`\`json
{
  "store_name": [{"l": 0, "w": 0}, {"l": 0, "w": 1}],
  "date": [{"l": 2, "w": 5}],
  "time": [],
  "phone_number": [],
  "total_amount": [],
  "taxes": [],
  "address": [],
  "line_item": [{"l": 5, "w": 1}],
  "line_item_name": [{"l": 5, "w": 2}],
  "line_item_price": [{"l": 5, "w": 3}]
}
\`\`\`
Return only this JSON structure. Nothing else.
`;
  return <pre>{prompt}</pre>;
}

function Receipt() {
  return (
    <div>
      <h1>The Project</h1>
      <p>
        In middle school, I spent a summer manually organizing my dad's receipts
        for tax purposes. It allowed me to buy a couple games on Xbox, but it
        was incredibly tedious. Automating this process was not an option at the
        time. After getting the ChatGPT Pro subscription, my programming skills
        have increased exponentially. I decided to automate not only the process
        of organizing receipts, but also the process of software development.
      </p>

      <h1>Strategy</h1>
      <p>
        I started with a solid foundation in AWS and a bit of React, but Swift
        was uncharted territory. Leveraging ChatGPT's o1 Pro Mode "reasoning," I
        laid out an initial strategy that bridged my existing skills with the
        challenge of integrating OCR data into a coherent system. This approach
        allowed me to jumpstart the project and quickly put together the
        essential components.
      </p>
      <p>
        While the initial strategy provided a useful roadmap, the development
        process was highly iterative. I continuously refined my approach based
        on feedback and rigorous testing. My background in AWS system design,
        React development, and QA testing enabled me to spot issues early and
        iterate rapidly.
      </p>
      <h1>Implementation</h1>

      <h2>OCR and Image Processing</h2>
      <p>
        I started with Tesseract for OCR, but it didn't capture the necessary
        details. Switching to Apple's Vision OCR provided more precise data,
        including bounding boxes and confidence metrics. I then implemented a
        clustering approach to group and normalize the varying OCR outputs,
        ensuring consistency across the receipt data.
      </p>
      <p>
        I applied DBSCAN clustering to group nearby text elements by focusing on
        their X-axis values, since receipts are taller than they are wide. This
        approach helped determine which words belong to which receipt by
        clustering similar X coordinates. Once isolated, I normalized the OCR
        coordinates to a standard 1x1 square, ensuring consistent scaling for
        subsequent calculations.
      </p>
      <ImageBoundingBox />
      <h2>Infrastructure</h2>
      <p>
        I had experience with AWS services and Terraform, but Pulumi was new to
        me. With ChatGPT, an OpenAI product, I was able to quickly create cloud
        services that are consistent between a "development" and "production"
        environment. This stopped me from "testing in production" while quickly
        optimizing cloud compute.
      </p>
      <div className="logos-container">
        <Pulumi />
        <OpenAI />
      </div>
      <p>
        I used a few AWS services to organize the data and serve it to the
        frontend:
      </p>
      <ul>
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

      <h2>ChatGPT</h2>
      <p>
        One of the core aspects of this project involved using ChatGPT's API to
        label receipt words automatically. I iterated through several prompt
        engineering strategies—testing everything from the structure of the JSON
        output to how ChatGPT highlights each word's location—to arrive at a
        final prompt that gave consistently accurate labels. This iterative
        process involved tweaking the prompt, sending sample payloads to
        ChatGPT's API, and validating the output against real receipts.
      </p>

      <code>
        <GPTPrompt />
      </code>

      <p>
        Refining my prompts revealed the power of prompt engineering with
        ChatGPT. By adjusting phrasing, specificity, and output constraints, I
        guided the model to accurately label receipt words and generate
        functional code that fit seamlessly into the project. This iterative
        process of testing, tweaking, and retesting was essential for harnessing
        ChatGPT's capabilities and producing reliable, maintainable code.
      </p>

      <h2>Frontend</h2>
      <p>
        The front end was built using TypeScript with React. The typing system
        in TypeScript made it easier for ChatGPT to gain context on the data
        being passed around. As I iterated on both the data processing and
        infrastructure, the frontend evolved alongside these changes.
      </p>
      <div className="logos-container">
        <ReactLogo />
        <TypeScriptLogo />
      </div>
      <p>
        Each refinement was continuously applied to and tested against the
        frontend through a CICD pipeline. This allowed me to quickly test new
        features and ensure that the frontend was always in sync and functioning
        correctly.
      </p>

      

      <h1>Results</h1>
      <p>
        And that's it! Now I have a system that scans, annotates, and stores
        receipt data, letting me explore it any way I like. It's been a fun
        crash course in everything from OCR to React to Pulumi—definitely worth
        the effort of digging out those old receipts.
      </p>

      <ReceiptStack />
      <ReceiptWords />
    </div>
  );
}

export default Receipt;
