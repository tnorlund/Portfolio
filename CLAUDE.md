# MCP Development Server

This repository includes a simple Model Context Protocol server for running
common development tasks.

Start the server from the repository root:

```bash
python mcp_server.py
```

The server exposes two tools:

- `run_tests` &ndash; run pytest for a specific package (`receipt_label`,
  `receipt_dynamo`, etc.) using all available CPU cores.
- `run_lint` &ndash; format code with `isort` and `black` and then run `pylint`
  and `mypy` for the package.

Both tools execute commands inside the package directory so configuration files
are discovered correctly.
