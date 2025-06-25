# Portfolio

This is Tyler Norlund's portfolio. It is a static website hosted on S3 and served through CloudFront. The website is a portfolio of projects and is built using React.

## `infra/`

The Pulumi project that defines the infrastructure for the portfolio.

## `portfolio/`

This React project. It is a portfolio of projects that Tyler has worked on.

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
