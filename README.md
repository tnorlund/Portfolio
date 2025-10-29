# Portfolio & Receipt Processing System

Full-stack applications demonstrating modern web development, machine learning, and cloud infrastructure expertise.

**Live Demo**: [tylernorlund.com](https://tylernorlund.com)

## 🚀 Quick Start

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
pip install -e "receipt_label[test]"
pytest receipt_label/tests/ -v
```

### Infrastructure Deployment

```bash
cd infra
pulumi stack select dev
pulumi up
```

## 📁 Project Structure

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
├── infra/            # Pulumi infrastructure
│   ├── __main__.py   # Infrastructure entry point
│   └── lambda_functions/  # Serverless functions
│
├── scripts/          # Utility scripts
└── docs/             # Documentation
    ├── architecture/ # System architecture docs
    ├── development/  # Development guides
    └── operations/   # Deployment and ops guides
```

## 🛠 Tech Stack

**Frontend**: Next.js 14, React, TypeScript, Tailwind CSS  
**Backend**: Python 3.12, FastAPI, OpenAI GPT-4, AWS Lambda  
**Database**: DynamoDB, S3, ChromaDB  
**Infrastructure**: AWS (CloudFront, Lambda, API Gateway, Step Functions), Pulumi  
**ML/AI**: OpenAI API, Custom OCR pipelines, scikit-learn  
**OCR Processing**: Swift, Apple Vision Framework, SQS queues

## 🧾 Receipt Processing System

An intelligent document processing pipeline that extracts structured data from receipt images using OCR and machine learning.

### Key Features

- **Automated Text Extraction**: Swift-based OCR using Apple's Vision framework
- **Intelligent Field Detection**: GPT-4 powered extraction of merchant, total, date, items
- **Merchant Validation**: Automated merchant name normalization and validation
- **Vector Search**: ChromaDB integration for semantic similarity search
- **RESTful API**: Complete API for receipt management and querying

### Swift OCR Processing

High-performance OCR processing using Apple's Vision framework with SQS queue-based job processing.

**Quick Start**:
```bash
# Build the Swift OCR worker (one-time setup)
cd receipt_ocr_swift
swift build --configuration release

# Run OCR worker with SQS queue processing
.build/release/receipt-ocr \
  --ocr-job-queue-url "<queue-url>" \
  --ocr-results-queue-url "<results-queue-url>" \
  --dynamo-table-name "<table-name>" \
  --region "us-east-1" \
  --continuous

# Or process a single image directly
swift receipt_upload/receipt_upload/OCRSwift.swift /tmp/output image.png
```

**Requirements**:
- macOS (for Apple Vision framework)
- Swift 5.9+
- AWS credentials configured

## 💻 Development

### Code Formatting

```bash
make format  # Runs black and isort
```

### Testing

```bash
# Install test dependencies
pip install -e "receipt_label[test]"

# Run Python tests
pytest receipt_label/tests/ -v
pytest receipt_label/tests/ -m "not integration"

# Run tests for specific package
./scripts/test_runner.sh receipt_dynamo

# JavaScript tests
cd portfolio && npm test
```

### Common Tasks

**Format code:**
```bash
make format  # Runs black and isort
```

**Run tests:**
```bash
./scripts/test_runner.sh receipt_dynamo
```

**Deploy infrastructure:**
```bash
cd infra && pulumi up
```

## 📚 Documentation

Detailed documentation is available in the [`docs/`](docs/) directory:

- **[Architecture Overview](docs/architecture/overview.md)** - System design and architecture
- **[Development Setup](docs/development/setup.md)** - Complete development environment setup
- **[Testing Guide](docs/development/testing.md)** - Testing strategies and best practices
- **[Deployment Guide](docs/operations/deployment.md)** - Production deployment procedures

### Key Documentation Files

- [System Architecture](docs/architecture/overview.md)
- [Complete Flow Documentation](docs/architecture/COMPLETE_FLOW_DOCUMENTATION.md) - End-to-end receipt processing flow
- [Testing Strategy](docs/development/TESTING_STRATEGY.md)
- [Lambda Networking](docs/architecture/LAMBDA_NETWORKING_ARCHITECTURE.md)
- [ChromaDB Architecture](docs/chromadb-efs-architecture.md)

## 🏗 Infrastructure

Infrastructure is managed with Pulumi (Python). Key components:

- **API Gateway** - RESTful API endpoints
- **Lambda Functions** - Serverless compute
- **Step Functions** - Workflow orchestration
- **DynamoDB** - NoSQL database
- **S3** - Object storage
- **CloudFront** - CDN distribution
- **SQS** - Message queues
- **EFS** - Shared file system for ChromaDB

### Infrastructure Commands

```bash
cd infra

# Preview changes
pulumi preview

# Deploy changes
pulumi up

# View stack outputs
pulumi stack output

# Switch stacks
pulumi stack select dev
pulumi stack select prod
```

## 🔧 Configuration

### Environment Variables

Python packages use environment variables for AWS configuration. Set these in your shell or `.env` file:

```bash
export AWS_REGION=us-east-1
export AWS_PROFILE=your-profile  # Optional
```

### Package Installation

All `receipt_*` packages use editable installs:

```bash
pip install -e receipt_dynamo
pip install -e receipt_label
pip install -e receipt_upload
```

## 📦 Packages

### receipt_dynamo
DynamoDB data access layer. Provides entities and client for interacting with receipt data.

### receipt_label
ML-based receipt analysis and labeling. Uses GPT-4 for intelligent field extraction.

### receipt_upload
OCR and image processing. Handles text extraction and spatial analysis.

### receipt_ocr_swift
Swift-based OCR worker using Apple Vision framework for high-performance text extraction.

## ⚠️ Important Notes

- **Package Separation**: Each `receipt_*` package has specific responsibilities. Don't mix concerns.
- **AWS Resources**: Most operations use DynamoDB, S3, and Lambda
- **Cost Optimization**: Keep AWS costs under $5/month
- **CI/CD**: GitHub Actions with self-hosted runners for cost savings
- **Python Version**: Requires Python 3.12+

## 📄 License

MIT License - see LICENSE file for details.

## 👤 Contact

Tyler Norlund  
- **GitHub**: [tnorlund](https://github.com/tnorlund)
- **LinkedIn**: [tyler-norlund](https://www.linkedin.com/in/tyler-norlund/)
- **Portfolio**: [tylernorlund.com](https://tylernorlund.com)
