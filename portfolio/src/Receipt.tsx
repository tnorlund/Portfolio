// import ReceiptGrid from "./ReceiptGrid";
import Diagram from "./Diagram";
import Pulumi from "./Pulumi";
import OpenAI from "./OpenAI";
import ReceiptStack from "./ReceiptStack";
import "./Receipt.css";

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
        I used my new ChatGPT subscription to help me write some Swift code to write the OCR results to a JSON. I have no experience with Swift, but I was able to get it working.
      </p>
      <p>
        From here, I developed a DynamoDB table schema to store the image and OCR data.
      </p>
      <p>
        I then created a Pulumi project to host all the AWS services needed. I'm currently using Lambda to process the image and OCR JSON, DynamoDB, S3 for data storage, API Gateway to access the OCR data, and CloudFront as a CDN. All of this allows me to host the data and images on the web.
      </p>

      <Diagram />
      <p>
        With the data in DynamoDB, I created a React app to display the data. This is where a lot of the fine tuning went in. I refined how I was storing the data and how I was processing it. With the help of ChatGPT, I was able to write a lot of the code in a very short amount of time.
      </p>
      <ReceiptStack />
      <p>
        Now comes the data science... The data is there, but what can I do with it? I decided to now use OpenAI's ChatGPT API for data annotation. I defined a receipt as having:
      </p>
      <ul>
        <li>Line Items</li>
        <li>Date</li>
        <li>Time</li>
        <li>Subtotal</li>
      </ul>
      <p>
        I use ChatGPT to label the different words in the OCR data. This process can be very expensive, but since the OCR has already been processed, I can send just the text and where it is on the receipt. This cost effective approach allows me to label the data in a very short amount of time.
      </p>
    </div>
  );
}

export default Receipt;
