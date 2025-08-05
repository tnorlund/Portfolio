# Portfolio & Receipt Processing System

A full-stack application showcasing modern web development and cloud infrastructure expertise, featuring a React portfolio website and an AWS-powered receipt processing pipeline.

## 🎯 Overview

This repository contains two main projects:

### Portfolio Website
A responsive React/Next.js portfolio showcasing projects and professional experience, deployed on AWS S3/CloudFront.

### Receipt Processing System  
An enterprise-grade OCR and ML pipeline for receipt digitization and analysis, built with Python and AWS services.

## 🏗️ Architecture

```
Portfolio/
├── portfolio/          # React/Next.js portfolio website
├── infra/             # Pulumi infrastructure as code
├── receipt_dynamo/    # DynamoDB data layer
├── receipt_label/     # ML labeling and analysis
├── receipt_upload/    # OCR processing pipeline
└── docs/              # Project documentation
```

## 🚀 Quick Start

### Prerequisites
- Node.js 18+
- Python 3.12+
- AWS CLI configured
- Pulumi CLI

### Portfolio Website
```bash
cd portfolio
npm install
npm run dev
```

### Receipt Processing
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e receipt_dynamo -e receipt_label
```

### Infrastructure
```bash
cd infra
pulumi up
```

## 📚 Documentation

- [Architecture Overview](docs/architecture/overview.md)
- [Development Setup](docs/development/setup.md)
- [Testing Guide](docs/development/testing.md)
- [Deployment Guide](docs/operations/deployment.md)

## 🛠️ Technologies

**Frontend**: React, Next.js, TypeScript, Tailwind CSS  
**Backend**: Python, FastAPI, OpenAI API, DynamoDB  
**Infrastructure**: AWS (Lambda, S3, CloudFront, DynamoDB), Pulumi  
**ML/AI**: OpenAI GPT-4, Custom OCR pipelines, Pattern detection  
**Testing**: Jest, Pytest, Playwright  

## 📊 Key Features

- **High Performance**: 4x faster test execution through intelligent parallelization
- **Cost Optimized**: Automated cost monitoring keeps cloud expenses under $5/month
- **Production Ready**: Comprehensive error handling, monitoring, and logging
- **AI-Enhanced**: Dual AI code review system for quality assurance
- **Scalable Architecture**: Serverless design handles variable workloads efficiently

## 🤝 Contributing

See [CONTRIBUTING.md](docs/CONTRIBUTING.md) for development guidelines.

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

## 👤 Author

Tyler Norlund - [GitHub](https://github.com/tnorlund) | [Portfolio](https://tylernorlund.com)