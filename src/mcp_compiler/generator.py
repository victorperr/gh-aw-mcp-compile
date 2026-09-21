from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from .analyzer import manifest


def _adapter(tool: dict, source_root: Path) -> str:
    module_name = ".".join(Path(tool["source_file"]).with_suffix("").parts)
    secret_lines = "\n".join(
        f"    _ = os.environ.get({secret!r})" for secret in tool["secrets"])
    return f'''import importlib
import json
import os
import sys


MODULE = {module_name!r}
TOOL = {tool["name"]!r}
SOURCE_ROOT = {str(source_root)!r}


def main() -> None:
    payload = json.load(sys.stdin)
{secret_lines or "    pass"}
    if not isinstance(payload, dict):
        raise TypeError("tool input must be a JSON object")
    sys.path.insert(0, SOURCE_ROOT)
    function = getattr(importlib.import_module(MODULE), TOOL)
    result = function(**payload)
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
'''


def _workflow(portable: list[dict], source_root: Path, workflow_name: str) -> str:
    source_path = Path(os.path.relpath(source_root, Path.cwd())).as_posix()
    tools = []
    for tool in portable:
        inputs = []
        for name, schema in tool["schema"]["properties"].items():
            required = name in tool["schema"].get("required", [])
            inputs.append(
                f"        {name}:\n          type: {schema['type']}\n          required: {'true' if required else 'false'}")
        input_block = "\n".join(inputs) or "        # No inputs"
        secrets = "\n".join(
            f"      {secret}: ${{{{ secrets.{secret} }}}}"
            for secret in tool["secrets"]
        )
        module = ".".join(Path(tool["source_file"]).with_suffix("").parts)
        tools.append(f'''  {tool['name']}:
    description: {json.dumps(tool['description'])}
    inputs:
{input_block}
    py: |
      import importlib
      import json
      import sys

            sys.path.insert(0, {json.dumps(source_path)})
      function = getattr(importlib.import_module({module!r}), {tool['name']!r})
      result = function(**inputs)
      print(json.dumps(result, default=str))
    env:
{secrets or '      # No secrets required.'}
    timeout: 60''')
    return f'''---
name: {json.dumps(f"GitHub Agentic Workflow - {workflow_name}")}
on: workflow_dispatch
engine: copilot
mcp-scripts:
''' + "\n".join(tools) + '''
---

Use the generated read-only tools when their results are needed. Mutating or stateful tools are intentionally excluded from this workflow.
'''


def _report(data: dict) -> str:
    tools = data["tools"]
    portable = [tool for tool in tools if tool["classification"] == "portable"]
    lines = ["# MCP Compile Report", "", "## Positioning", "", "> Compile your MCP server into the cheapest secure execution target.",
             "", "## Tool matrix", "", "| Tool | Classification | Latency | Estimated runner cost | Secrets |", "|---|---|---:|---:|---|"]
    for tool in tools:
        lines.append(
            f"| `{tool['name']}` | {tool['classification']} | {tool['estimated_latency_ms']} ms | ${tool['estimated_cost_usd']:.3f} | {', '.join(tool['secrets']) or 'none'} |")
    lines += ["", "## Economics", "", f"- Portable tools generated: **{len(portable)} / {len(tools)}**", "- Cost model: approximate GitHub-hosted runner minute allocation, excluding API costs and dependency installation.", "- Latency model: cold-start estimate plus dependency and external API variability.", "- Production recommendation: use this target for low-volume, stateless, asynchronous tools; use a warm runner or edge target for high-frequency calls.",
              "", "## Security", "", "- Secrets are referenced by name only and must be mapped explicitly in repository or environment secrets.", "- The generated Agentic Workflow exposes only portable, read-only tools; inferred permissions remain in the manifest for review.", "- Untrusted pull requests must not receive production secrets.", "- Review generated source dependencies before allowing network access."]
    return "\n".join(lines) + "\n"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "mcp-tools"


def generate(repository: str | Path, output: str | Path, name: str | None = None) -> dict:
    source_root = Path(repository).resolve()
    destination = Path(output).resolve()
    workflow_name = name or source_root.name
    workflow_slug = _slug(workflow_name)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    data = manifest(source_root)
    portable = []
    tools_dir = destination / "tools"
    tools_dir.mkdir()
    for tool in data["tools"]:
        if tool["classification"] == "portable":
            portable.append(tool)
            (tools_dir / f"{tool['name']}.py").write_text(
                _adapter(tool, source_root), encoding="utf-8")
    data["generated_tools"] = [tool["name"] for tool in portable]
    (destination / "manifest.json").write_text(json.dumps(data,
                                                          indent=2) + "\n", encoding="utf-8")
    workflow = destination / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / f"{workflow_slug}.md").write_text(
        _workflow(portable, source_root, workflow_name), encoding="utf-8")
    (destination / "REPORT.md").write_text(_report(data), encoding="utf-8")
    (destination / "SECURITY.md").write_text("# Security review\n\nReview every generated permission and map only the named secrets required by each tool. Do not expose secrets to forked pull requests.\n", encoding="utf-8")
    tests = destination / "tests"
    tests.mkdir()
    (tests / "test_contracts.py").write_text(_contract_tests(data,
                                                             source_root), encoding="utf-8")
    return data


def _contract_tests(data: dict, source_root: Path) -> str:
    cases = {
        tool["name"]: {
            name: {"string": "example", "integer": 1, "number": 1.0, "boolean": True,
                   "array": [], "object": {}}.get(schema.get("type"), None)
            for name, schema in tool["schema"]["properties"].items()
        }
        for tool in data["tools"]
        if tool["classification"] == "portable"
    }
    modules = {
        tool["name"]: ".".join(Path(tool["source_file"]).with_suffix("").parts)
        for tool in data["tools"]
        if tool["classification"] == "portable"
    }
    return f'''import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
CASES = {cases!r}
MODULES = {modules!r}
SOURCE_ROOT = {str(source_root)!r}


def test_manifest_has_generated_tools():
    data = json.loads((Path(__file__).parents[1] / "manifest.json").read_text())
    assert set(data["generated_tools"]) <= {{tool["name"] for tool in data["tools"]}}


def test_generated_tools_match_source_contract():
    for tool in json.loads((Path(__file__).parents[1] / "manifest.json").read_text())["tools"]:
        if tool["classification"] != "portable":
            continue
        assert tool["schema"]["type"] == "object"
        adapter = Path(__file__).parents[1] / "tools" / f"{{tool['name']}}.py"
        sys.path.insert(0, SOURCE_ROOT)
        original = getattr(importlib.import_module(MODULES[tool["name"]]), tool["name"])(**CASES[tool["name"]])
        payload = json.dumps(CASES[tool["name"]]).encode()
        result = subprocess.run([sys.executable, str(adapter)], input=payload, capture_output=True, env=os.environ.copy())
        assert result.returncode == 0, result.stderr.decode()
        assert json.loads(result.stdout) == original
'''
