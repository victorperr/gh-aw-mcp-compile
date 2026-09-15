# MCP Compile

**Compile your MCP server into the cheapest secure execution target.**

MCP Compile is an early-stage command-line tool for turning portable Python MCP
tools into GitHub-native, ephemeral execution artifacts. It is useful for
exploring portability, permissions, secrets, and runner-cost tradeoffs before
deploying a long-lived service.

This MVP accepts a Python FastMCP repository, discovers `@mcp.tool` functions, classifies portability, and generates GitHub-native JSON-in/JSON-out adapters for portable tools.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
mcp-compile compile examples\sample-server --out .mcp-build
mcp-compile contract .mcp-build
```

The generated bundle contains:

- `manifest.json`: discovered schemas, portability, secrets, permissions, cost and latency estimates
- `.github/workflows/mcp-tools.md`: GitHub Agentic Workflows `mcp-scripts` entry point
- `tools/<tool>.py`: ephemeral adapter invoked with JSON on stdin
- `tests/test_contracts.py`: contract tests against the source implementation and adapter
- `SECURITY.md`: generated least-privilege review
- `REPORT.md`: cost, latency, and security report

## Supported MVP shape

The analyzer recognizes Python functions decorated with `@mcp.tool`, `@server.tool`, or `@tool`. It supports primitive JSON-compatible annotations, lists, dictionaries, optional values, and synchronous functions. Tools using files, subprocesses, mutable global state, unsupported annotations, or asynchronous functions are marked partially portable or incompatible rather than silently generated.

The generated workflow follows GitHub Agentic Workflows' `mcp-scripts` model: tools run on the Actions runner for the duration of an agentic workflow, with explicitly mapped secrets and no persistent server to operate. GitHub Actions still has runner-minute costs and cold starts; the generated report makes those tradeoffs visible. Treat the generated workflow as read-only: mutating tools belong in safe outputs or another audited execution target.

## Development

```powershell
pip install -e ".[test]"
python -m pytest -q
```

The project supports Python 3.10 and newer. The sample server is deliberately
self-contained, so no MCP runtime dependency is needed to run the analyzer or
tests.

## Security

Generated workflows execute on a GitHub Actions runner outside the agent
sandbox. Review generated source, permissions, dependencies, and secret
mappings before use. Never expose production secrets to untrusted pull
requests. See [SECURITY.md](SECURITY.md) for vulnerability reporting and
security expectations.

## Project status

This is an MVP. Generated artifacts are intended for review and experimentation,
not unattended production deployment. The analyzer is conservative but static:
it cannot prove that a tool is safe or that an external dependency is benign.

## License

MCP Compile is released under the [MIT License](LICENSE).
