// import ReceiptGrid from "./ReceiptGrid";
import Diagram from "./Diagram";
import Pulumi from "./Pulumi";
import OpenAI from "./OpenAI";
import ReceiptStack from "./ReceiptStack";
import "./Receipt.css";

function GPTPrompt() {
  const prompt = `You are a helpful assistant that extracts structured data from a receipt.
The receipt's OCR text is:
{
  "receipt": { ... },
  "words": [
    {
      "text": "SPROUTS",
      "centroid": {"x": 0.1, "y": 0.1}
    },
    ...
  ]
}

**Your task**: Identify the following fields and output them as valid JSON:
    - store_name (string)
    - date (string)
    - time (string)
    - phone_number (string)
    - total_amount (number)
    - items (array of objects with fields: "item_name" (string) and "price" (number))
    - taxes (number)
    - address (string)

Additionally, for **every field** you return, **please include**:
1) The field's **value** (e.g. "SPROUTS FARMERS MARKET").
2) An array of "word_centroids" that correspond to the OCR words. 
     - This array should list the (x, y) coordinates of each word that you used to form that field's value.
     - Use the same centroids from the "words" array above.

If a particular field is not found, return an empty string or null for that field.

**The JSON structure** should look like this (conceptually):
\`\`\`json
{
  "store_name": {
    "value": "...",
    "word_centroids": [
      {"x": ..., "y": ...},
      ...
    ]
  },
  ...
  "items": [
    {
      "item_name": {
        "value": "...",
        "word_centroids": [...]
      },
      "price": {
        "value": 0.0,
        "word_centroids": [...]
      }
    }
  ],
}
\`\`\`
IMPORTANT: Make sure your output is valid JSON, with double quotes around keys and strings.
`;
  return <pre>{prompt}</pre>;
}

function Receipt() {
  return (
    <div>
      <h1>What it is</h1>
      <p>
        I've been saving my receipts for a while. Without much thought, I've
        scanned them into my computer and stored them for many, many years.
        While sick in bed, I decided to start a new project.
      </p>
      <p>
        I wanted to start using Pulumi with Terraform losing its appeal, and I
        wanted to create something using ChatGPT. I ended up buying a ChatGPT
        Pro subscription for Christmas. ChatGPT with VSCode allowed me to move
        very quickly.
      </p>

      <div className="logos-container">
        <Pulumi />
        <OpenAI />
      </div>

      <p>
        I got to work. I did some research first looking into OCR. I started
        using Tesseract, but it didn't give very detailed output. After trying
        out a few and considering cost, I ended up going with Apple's Vision
        OCR. This was introduced in 2019 and provides:
      </p>
      <ul>
        <li>Text recognition</li>
        <li>Bounding Boxes</li>
        <li>Angles</li>
        <li>Confidence Scores</li>
      </ul>
      <p>
        I used my new ChatGPT subscription to help me write some Swift code to
        write the OCR results to a JSON. I have no experience with Swift, but I
        was able to get it working.
      </p>
      <p>
        From here, I developed a DynamoDB table schema to store the image and
        OCR data.
      </p>
      <p>
        I then created a Pulumi project to host all the AWS services needed. I'm
        currently using Lambda to process the image and OCR JSON, DynamoDB, S3
        for data storage, API Gateway to access the OCR data, and CloudFront as
        a CDN. All of this allows me to host the data and images on the web.
      </p>
      <Diagram />
      <p>
        With the data in DynamoDB, I created a React app to display the data.
        This is where a lot of the fine tuning went in. I refined how I was
        storing the data and how I was processing it. With the help of ChatGPT,
        I was able to write a lot of the code in a very short amount of time.
      </p>
      <ReceiptStack />
      <p>
        Now comes the data science… The data is there, but what can I do with
        it? I decided to use OpenAI's ChatGPT API for data annotation. My first
        task was creating a structured prompt—like the one below—so it would
        consistently return well-formatted JSON. After some prompt engineering
        research, I landed on a design that ties each field to specific word
        coordinates from the OCR output, allowing me to visualize and process
        the data accurately.
      </p>
      <code>
        <GPTPrompt />
      </code>
    </div>
  );
}

export default Receipt;
