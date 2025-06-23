# MCP Development Server

This repository includes a simple Model Context Protocol server for running
common development tasks.

Start the server from the repository root:

```bash
python mcp_server.py
```

The server exposes nine tools:

### Development Tools

- `run_tests` &ndash; run pytest for a specific package (`receipt_label`,
  `receipt_dynamo`, etc.) using all available CPU cores.
- `run_lint` &ndash; format code with `isort` and `black` and then run `pylint`
  and `mypy` for the package.

### Pulumi Infrastructure Tools

- `pulumi_preview` &ndash; run `pulumi preview` in the infra directory to see
  planned infrastructure changes. Optionally specify a stack and additional args.
- `pulumi_up` &ndash; run `pulumi up` in the infra directory to apply
  infrastructure changes. Optionally specify a stack and additional args.
- `pulumi_stack_list` &ndash; list all available Pulumi stacks.
- `pulumi_stack_output` &ndash; get outputs from a Pulumi stack. Optionally
  specify the stack name and a specific output name.
- `pulumi_refresh` &ndash; refresh Pulumi state to match actual infrastructure.
  Optionally specify a stack and additional args.
- `pulumi_logs` &ndash; get logs from the last Pulumi operation. Optionally
  specify a stack and additional args.
- `pulumi_config_get` &ndash; get a configuration value from Pulumi. Requires
  a key parameter, optionally specify a stack.

The test and lint tools execute commands inside the package directory so
configuration files are discovered correctly. All Pulumi tools execute in the
infra directory.
