# GitHub Agentics Workflows MCP Compile

> _Compile your MCP server into the cheapest secure execution target_

<!-- TABLE OF CONTENTS -->
<details>
  <summary>Table of Contents</summary>
  <ol>
    <li>
      <a href="#about-the-project">About The Project</a>
    </li>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#installation">Installation</a></li>
      </ul>
    </li>
    <li><a href="#requirements">Requirements</a></li>
    <li><a href="#run-the-demo">Run the demop</a></li>
    <li><a href="#development">Development</a></li>
    <li><a href="#security">Security</a></li>
    <li><a href="#license">License</a></li>
  </ol>
</details>


<!-- ABOUT THE PROJECT -->
## 🚀 About The Project

`gh-aw-mcp-compile` is an **early-stage command-line tool** for **turning portable Python MCP tools into GitHub-native, ephemeral execution artifacts**. It helps decide which MCP tools can safely run as ephemeral [GitHub Agentic Workflow](https://github.github.com/gh-aw/) tools instead of requiring a permanent MCP server.

This MVP accepts a Python [FastMCP](https://gofastmcp.com/) repository, discovers `@mcp.tool` functions, classifies portability, and generates GitHub Agentic Workflow mcp-scripts artifacts for tools classified as portable. 

It also produces a manifest, security review, cost/latency estimates, adapters, and contract tests.

The FastMCP server is the input application. The generated `mcp-scripts` workflow is the deployment target for simple, read-only tools.

<!-- GETTING STARTED -->
## Getting Started

### Prerequesites

- **Python 3.10+**, in an environment where the server's own dependencies are already installed. 
- **GitHub CLI** with the **gh-aw extension** (gh extension install github/gh-aw), plus the credentials for the AI engine they choose (Copilot by default).
- A target **FastMCP server** written in Python, stored in a GitHub repository with Actions turned on.

### Installation

```powershell
# Install the compiler in the server's environment
pip install git+https://github.com/<you>/mcp-deploy 

# Check which tools can be moved, from inside your repository
mcp-compile inspect path\to\server

# Generate the workflow bundle and test it
mcp-compile compile path\to\server --out .mcp-build --name "My Server"
mcp-compile contract .mcp-build
```
Review the output in `.mcp-build`: Read `.mcp-build/REPORT.md` (the per-tool findings) and `.mcp-build/SECURITY.md` (the secrets to add and a checklist).

Copy the workflow files into your repository
   - `.mcp-build/.github/workflows/<name>.md`
   - `.mcp-build/.github/workflows/shared/<name>-tools.md`


**Add the secrets** listed in `SECURITY.md` under **Settings > Secrets and variables > Actions**. The sample server, for example, needs `ACCOUNTS_API_KEY`. Using a protected environment is better, and forked pull requests should never get these secrets.

**Compile and commit.** gh-aw turns the `.md` workflow into a `.lock.yml` file, and both files must be committed:


Notes:

- Only **read-only tools** are moved. Tools that write things stay on your existing server or become Safe Outputs.
- Each tool call starts a **fresh process on the Actions runner**, and runner minutes are billed. This suits occasional, stateless tools, not high-volume traffic.
- The read-only check is static. It doesn't follow code in other files or in libraries, so you should still review what gets generated.



<!-- REQUIREMENTS -->
## Requirements

The analyzer detects tools defined with FastMCP: any function decorated with `@mcp.tool`, `@mcp.tool(...)` or `@tool`.

It handles:

- **Types:** primitives, `list`, `dict`, `Optional`, `Literal` (converted to `enum`) and `Annotated`/`Field` descriptions
- **Defaults and docs:** default values, plus argument descriptions from Google-style docstrings
- **Async:** both regular and `async` functions
- **Secrets:** environment variables read via `os.getenv` or `os.environ`, mapped to `env:`
- **Dependencies:** third-party imports, pinned to your installed versions and listed under `dependencies:`

`Context` parameters are ignored, and FastMCP itself isn't installed on the runner.

MCP scripts run on the runner outside the agent sandbox and must be read-only. A tool is **incompatible**, and is not generated, when static analysis finds a mutation in it or in a same-module helper: file writes, `shutil`/`os` file changes, HTTP `POST`/`PUT`/`PATCH`/`DELETE`, mutating SQL, subprocesses, or `readOnlyHint=False`/`destructiveHint=True` annotations. Such tools belong in safe outputs. Tools that read the runner filesystem, keep global state or import packages that cannot be pinned are **partially portable** and are also not generated.

The generated workflow follows **GitHub Agentic Workflows**  and its `mcp-scripts` model: tools run on the Actions runner for the duration of an agentic workflow, with explicitly mapped secrets and no persistent server to operate. Each call starts a fresh Python process, and runner minutes are billed per workflow run.

See [MCP Scripts documentation](https://github.github.com/gh-aw/reference/mcp-scripts/) for the contract this generator targets.

<!-- RUN THE DEMO -->
## Run the demo

The sample is a real FastMCP server, not a mock decorator. Install the test extra (which includes FastMCP), then inspect and compile it:

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

The workflow name and filename identify the source server. Without `--name`, the compiler uses the server directory name, for example `examples\sample-server` becomes `.github/workflows/sample-server.md`. For multiple servers, use separate output directories and explicit names:

```powershell
mcp-compile compile servers\billing --out .mcp-build\billing --name "Billing MCP"
mcp-compile compile servers\support --out .mcp-build\support --name "Support MCP"
```

This produces `billing.md` and `support.md` in their respective workflow
directories, with matching display names in each workflow's frontmatter.

Other `compile` options:

```powershell
mcp-compile compile examples\sample-server --engine claude --on push --timeout 90 --tool-timeout text_stats=300
```

The output directory is replaced on every compile, so the compiler refuses to write to a directory that contains the source, or to a non-empty directory it did not create.

To run the source MCP server directly over FastMCP's default stdio transport:

```powershell
python examples\sample-server\server.py
```

<!-- DEVELOPMENT -->
## Development

```powershell
pip install -e ".[test]"
python -m pytest -q
```

The project supports Python 3.10 and newer. The compiler itself uses static analysis and does not need FastMCP, but the sample server and generated contract tests import it; install the `test` or `demo` extra before running those parts. Run the compiler in the server's environment so its dependencies can be pinned.

<!-- SECURITY -->
## Security

Generated workflows execute on a GitHub Actions runner outside the agent sandbox. Review generated source, permissions, dependencies, and secret mappings before use. Never expose production secrets to untrusted pull requests. 

See [SECURITY.md](SECURITY.md) for vulnerability reporting and security expectations.


<!-- LICENSE -->
## License

MCP Compile is released under the [MIT License](LICENSE).



<details>
    <summary>List of improvements</summary>

        - The read-only check has limits. It only looks at the tool and helpers in the same file, not other files or libraries.
        - Generate safe-output jobs for the mutating tools that are excluded today;
</details>
