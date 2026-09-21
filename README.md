# MCP Compile

**"Compile your MCP server into the cheapest secure execution target."**

`gh-aw-mcp-compile` is an **early-stage command-line tool** for **turning portable Python MCP tools into GitHub-native, ephemeral execution artifacts**. It helps decide which MCP tools can safely run as ephemeral [GitHub Agentic Workflow](https://github.github.com/gh-aw/) tools instead of requiring a permanent MCP server.

This MVP accepts a Python [FastMCP](https://gofastmcp.com/) repository, discovers `@mcp.tool` functions, classifies portability, and generates GitHub Agentic Workflow mcp-scripts artifacts for tools classified as portable. It also produces a manifest, security review, cost/latency estimates, adapters, and contract tests.

The FastMCP server is the input application. The generated `mcp-scripts` workflow is the deployment target for simple, read-only tools.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
mcp-compile compile examples\sample-server --out .mcp-build --name "Sample Server"
mcp-compile contract .mcp-build
```

The generated bundle contains:

- `manifest.json`: discovered schemas, portability, secrets, permissions, cost and latency estimates
- `.github/workflows/<server-name>.md`: named GitHub Agentic Workflows `mcp-scripts` entry point
- `tools/<tool>.py`: ephemeral adapter invoked with JSON on stdin
- `tests/test_contracts.py`: contract tests against the source implementation and adapter
- `SECURITY.md`: generated least-privilege review
- `REPORT.md`: cost, latency, and security report

## Supported MVP shape

The analyzer recognizes Python functions decorated with `@mcp.tool`, `@server.tool`, or `@tool`. It supports primitive JSON-compatible annotations, lists, dictionaries, optional values, and synchronous functions. Tools using files, subprocesses, mutable global state, unsupported annotations, or asynchronous functions are marked partially portable or incompatible rather than silently generated.

The generated workflow follows **GitHub Agentic Workflows**  and its `mcp-scripts` model: tools run on the Actions runner for the
duration of an agentic workflow, with explicitly mapped secrets and no persistent server to operate. GitHub Actions still has runner-minute costs and cold starts; the generated report makes those tradeoffs visible. Treat the generated workflow as read-only: mutating tools belong in safe outputs or another audited execution target. See [MCP Scripts documentation](https://github.github.com/gh-aw/reference/mcp-scripts/) for the contract this generator targets.

## Run the demo

The sample is a real FastMCP server, not a mock decorator. Install the test
extra (which includes FastMCP), then inspect and compile it:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"

# Discover tools and their portability classification
mcp-compile inspect examples\sample-server

# Generate the GitHub Agentic Workflow bundle and run its contract tests
mcp-compile compile examples\sample-server --out .mcp-build --name "Sample Server"
mcp-compile contract .mcp-build
```

The workflow name and filename identify the source server. Without `--name`,
the compiler uses the server directory name, for example
`examples\sample-server` becomes `.github/workflows/sample-server.md`. For
multiple servers, use separate output directories and explicit names:

```powershell
mcp-compile compile servers\billing --out .mcp-build\billing --name "Billing MCP"
mcp-compile compile servers\support --out .mcp-build\support --name "Support MCP"
```

This produces `billing.md` and `support.md` in their respective workflow
directories, with matching display names in each workflow's frontmatter.

To run the source MCP server directly over FastMCP's default stdio transport:

```powershell
python examples\sample-server\server.py
```

That process waits for an MCP client. The compiler demo is usually more useful
for a first check because it shows the generated manifest, workflow, security
review, and adapter contract in one run.

## Development

```powershell
pip install -e ".[test]"
python -m pytest -q
```

The project supports Python 3.10 and newer. The compiler itself uses static analysis and does not need FastMCP, but the sample server and generated contract tests import it; install the `test` or `demo` extra before running those parts.

## Security

Generated workflows execute on a GitHub Actions runner outside the agent sandbox. Review generated source, permissions, dependencies, and secret mappings before use. Never expose production secrets to untrusted pull requests. See [SECURITY.md](SECURITY.md) for vulnerability reporting and security expectations.

## License

MCP Compile is released under the [MIT License](LICENSE).
