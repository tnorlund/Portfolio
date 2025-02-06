// import ReceiptGrid from "./ReceiptGrid";
import Diagram from "./Diagram";
import Pulumi from "./Pulumi";
import OpenAI from "./OpenAI";
import ReceiptStack from "./ReceiptStack";
import ReceiptWords from "./ReceiptWords";
import ImageBoundingBox from "./ImageBoundingBox";


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
        Ever open a drawer and find piles of old receipts you scanned ages ago,
        but never got around to using? That was me. I'd been accumulating
        digital copies for years—some more than a decade old—just in case I ever
        needed them. But I never really did anything with them… until now. I
        recently subscribed to ChatGPT Pro and wanted a fun project to try out
        Pulumi, so I thought, “Why not turn all these dusty scans into something
        useful?”
      </p>
      <ImageBoundingBox />
      <p>
        First up: extracting the text from each receipt. I started with
        Tesseract for OCR (Optical Character Recognition), but it wasn't giving
        me all the details I needed. Then I discovered Apple's Vision OCR. This
        tool not only reads text but also provides handy details like bounding
        boxes, angles, and even how confident it is about each line of text. I
        wrote a little Swift script—with a big assist from ChatGPT—to pump all
        that information into a JSON file.
      </p>

      <p>
        Next, I needed a place to store everything. That's where AWS DynamoDB
        came in handy: it's perfect for quick reads and writes, and I don't have
        to worry about managing servers. To tie it all together, I used Pulumi
        to create and maintain the infrastructure—AWS Lambda handles the image
        processing, S3 stores the images, API Gateway serves the data, and
        CloudFront makes it all run smoothly on the web. Switching from
        Terraform to Pulumi turned out to be simpler than I expected.
      </p>

      <p>
        Of course, data is only good if you can see it. So, I built a little
        React app that shows each receipt alongside its OCR text. ChatGPT helped
        generate a bunch of the React code, and I fine-tuned how that OCR data
        would display. Below is a quick diagram of how everything fits together:
      </p>

      <Diagram />

      <p>
        The real trick was getting ChatGPT to label each receipt field—store
        name, date, total, and so on—in a way that matched the exact words in
        the OCR. I learned it's vital to tell ChatGPT <em>not</em> to create new
        coordinates. If it makes up a coordinate that isn't really on the
        receipt, everything gets out of sync. With some patience (and a bunch of
        prompt engineering), I finally got ChatGPT to output perfect JSON,
        linking each piece of text to the right spot on the receipt.
      </p>

      <div className="logos-container">
        <Pulumi />
        <OpenAI />
      </div>

      <p>
        I also dealt with a ton of duplicate images—turns out having the ability
        to nuke the cloud environment and start again results in duplicate
        images. To fix this, each upload generates a unique ID, and I run a
        SHA256 hash to check if the file's already in the system. This makes the
        entire upload process “idempotent,” which is a fancy way of saying, “No
        matter how many times you do it, you'll get the same end result.”
        Because I had thorough tests in place, making these changes was pretty
        painless.
      </p>

      <p>
        Here's the ChatGPT prompt that does all the magic. It ensures each field
        (like store name, total, etc.) comes back with valid JSON and maps to
        the right words on the receipt. Notice how I tell ChatGPT not to invent
        new coordinates. That was key!
      </p>

      <code>
        <GPTPrompt />
      </code>

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
