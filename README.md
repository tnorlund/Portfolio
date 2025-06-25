# Portfolio

This is Tyler Norlund's portfolio. It is a static website hosted on S3 and served through CloudFront. The website is a portfolio of projects and is built using React.

**🚀 Enhanced with AI-Optimized Development Workflow**: This repository features a state-of-the-art development pipeline with 4x faster tests, dual AI code reviews, and cost-optimized automation.

## `infra/`

The Pulumi project that defines the infrastructure for the portfolio.

## `portfolio/`

This React project. It is a portfolio of projects that Tyler has worked on.

## 🚀 Advanced Development Features

### **Pytest Optimization System (4x Faster Tests)**
- **Intelligent Parallelization**: 62.8min → 15.8min test execution
- **Smart Test Splitting**: 39 integration files across 4 optimal parallel groups  
- **File Change Detection**: Skip unnecessary tests based on changed files
- **Advanced Caching**: Environment, dependencies, and test result caching

### **Dual AI Review System**
- **Cursor Bot**: Automated bug detection and security analysis
- **Claude Code**: Architectural review and performance optimization
- **Cost Optimized**: Smart model selection keeping costs $5-25/month
- **Fast Validation**: 30-second syntax checks before expensive AI reviews

### **Production-Ready Reliability**
- ✅ All critical bugs resolved (test masking, workflow triggers)
- ✅ Proper error propagation and failure detection
- ✅ Budget controls and usage monitoring
- ✅ Comprehensive documentation and guides

**Usage**:
```bash
# Run optimized tests locally
./scripts/test_runner.sh receipt_dynamo

# Check AI review costs
python scripts/cost_optimizer.py --check-budget

# AI reviews run automatically on PR creation
```

**Documentation**: See [`COMPREHENSIVE_IMPLEMENTATION_SUMMARY.md`](COMPREHENSIVE_IMPLEMENTATION_SUMMARY.md) for complete details.

## MCP servers

This repository uses [Model Context Protocol](https://github.com/modelcontextprotocol)
servers to streamline development. The Next.js server configuration lives in
`portfolio/mcp-server.js`. A new Python server entry `python-receipts` is
defined in `mcp-config.json` and launches `python mcp_server.py`.

### Required environment variables

The Python server expects several credentials to be available in the
environment:

```
OPENAI_API_KEY
PINECONE_API_KEY
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
GOOGLE_PLACES_API_KEY
```

Store these in your shell or a `.env` file before running the server.

The `python-receipts` server uses whichever `python` executable is first on
your `PATH`. Activate your `.venv` prior to launching the server so it runs
inside your virtual environment.
