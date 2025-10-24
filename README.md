# Portfolio

Full-stack applications demonstrating modern web development, machine learning, and cloud infrastructure expertise.

## Projects

### 🌐 Portfolio Website
A responsive, server-side rendered personal portfolio built with Next.js and React. Features optimized image loading, dynamic content rendering, and modern web performance best practices.

**Live Demo**: [tylernorlund.com](https://tylernorlund.com)

### 🧾 Receipt Processing System  
An intelligent document processing pipeline that extracts structured data from receipt images using OCR and machine learning. Processes receipts through text extraction, field detection, and merchant validation using GPT-4 and custom ML models.

**Key Features**:
- Automated text extraction from receipt images
- Intelligent field detection (merchant, total, date, items)
- Merchant validation and normalization
- RESTful API for receipt management

#### Swift OCR Processing
High-performance OCR processing using Apple's Vision framework with SQS queue-based job processing.

**Quick Start**:
```bash
# Build the Swift OCR worker (one-time setup)
cd receipt_ocr_swift
swift build --configuration release

# Run OCR worker with SQS queue processing
.build/release/receipt-ocr \
  --ocr-job-queue-url "https://sqs.us-east-1.amazonaws.com/681647709217/upload-images-dev-ocr-queue" \
  --ocr-results-queue-url "https://sqs.us-east-1.amazonaws.com/681647709217/upload-images-dev-ocr-results-queue" \
  --dynamo-table-name "ReceiptsTable-dc5be22" \
  --region "us-east-1" \
  --continuous

# Or process a single image directly
swift receipt_upload/receipt_upload/OCRSwift.swift /tmp/output image.png
```

**Requirements**:
- macOS (for Apple Vision framework)
- Swift 5.9+
- AWS credentials configured

### ☁️ Infrastructure as Code
Complete AWS infrastructure managed with Pulumi, including serverless functions, CDN distribution, and auto-scaling services.

## Tech Stack

**Frontend**: Next.js 14, React, TypeScript, Tailwind CSS  
**Backend**: Python 3.12, FastAPI, OpenAI GPT-4, AWS Lambda  
**Database**: DynamoDB, S3  
**Infrastructure**: AWS (CloudFront, Lambda, API Gateway), Pulumi  
**ML/AI**: OpenAI API, Custom OCR pipelines, scikit-learn  
**OCR Processing**: Swift, Apple Vision Framework, SQS queues  

## Getting Started

### Prerequisites
```bash
# Required
node >= 18.0.0
python >= 3.12
aws-cli (configured)

# Optional
pulumi (for infrastructure)
swift >= 5.9 (for OCR processing)
```

### Portfolio Website

```bash
cd portfolio
npm install
npm run dev
# Visit http://localhost:3000
```

### Receipt Processing System

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install packages
pip install -e receipt_dynamo
pip install -e receipt_label
pip install -e receipt_upload

# Run tests
pytest receipt_label/tests/unit
```

### Infrastructure Deployment

```bash
cd infra
pulumi stack select dev
pulumi up
```

## Project Structure

```
├── portfolio/          # Next.js portfolio website
│   ├── pages/         # Page components
│   ├── components/    # Reusable React components
│   └── public/        # Static assets
│
├── receipt_dynamo/    # DynamoDB data access layer
│   ├── entities/      # Data models
│   └── tests/         # Unit and integration tests
│
├── receipt_label/     # ML-based receipt analysis
│   ├── models/        # ML models and processors
│   └── pattern_detection/  # Text pattern recognition
│
├── receipt_upload/    # OCR and image processing
│   ├── ocr.py        # Text extraction
│   └── geometry.py   # Spatial analysis
│
├── receipt_ocr_swift/ # Swift OCR worker
│   ├── Sources/      # Swift source code
│   └── Package.swift # Swift package configuration
│
└── infra/            # Pulumi infrastructure
    ├── __main__.py   # Infrastructure entry point
    └── lambda_functions/  # Serverless functions
```

## Documentation

Detailed documentation available in the [`docs/`](docs/) directory:
- [Architecture Overview](docs/architecture/overview.md)
- [Development Guide](docs/development/setup.md)
- [API Documentation](docs/api/)

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Contact

Tyler Norlund - [GitHub](https://github.com/tnorlund) | [LinkedIn](https://www.linkedin.com/in/tyler-norlund/)