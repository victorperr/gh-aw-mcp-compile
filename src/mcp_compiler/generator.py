from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .analyzer import manifest, repository_root

MARKER = ".mcp-compile"
DEFAULT_TIMEOUT = 60
LARGE_OUTPUT_CHARACTERS = 500
SAMPLE_VALUES = {"string": "example", "integer": 1, "number": 1.0, "boolean": True, "array": [], "object": {}}

# Runs inside the gh-aw `py:` tool, where `inputs` is predefined.
SCRIPT_IMPORTS = '''import asyncio
import contextlib
import importlib
import inspect
import json
import os
import sys
'''
# FastMCP is replaced by a pass-through stub so the runner never installs the framework
# just to call a plain function.
FRAMEWORK_STUB = '''import types


class _Server:
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        def decorator(*args, **kwargs):
            if len(args) == 1 and callable(args[0]) and not kwargs:
                return args[0]
            return lambda function: function
        return decorator


for _name in {stubs!r}:
    _parts = _name.split(".")
    for _index in range(1, len(_parts) + 1):
        _path = ".".join(_parts[:_index])
        if _path not in sys.modules:
            _module = types.ModuleType(_path)
            _module.__getattr__ = lambda attribute: _Server
            sys.modules[_path] = _module
            if _index > 1:
                setattr(sys.modules[".".join(_parts[:_index - 1])], _parts[_index - 1], _module)

'''
SCRIPT_CALL = '''
sys.path.insert(0, os.path.join(os.environ.get("GITHUB_WORKSPACE", os.getcwd()), {source_path!r}))
with contextlib.redirect_stdout(sys.stderr):
    _function = getattr(importlib.import_module({module!r}), {function!r})
    _function = getattr(_function, "fn", _function)
    _result = _function(**{{key: value for key, value in inputs.items() if value is not None}})
    if inspect.isawaitable(_result):
        _result = asyncio.run(_result)
print(json.dumps(_result, default=str))
'''


class Block(str):
    """A string emitted as a YAML literal block scalar."""


def _scalar(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][\w./-]*", value)             and value.lower() not in {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}:
        return value
    # JSON scalars are valid YAML flow scalars and escape every special character.
    return json.dumps(value)


def _key(value: str) -> str:
    return value if re.fullmatch(r"[A-Za-z_][\w-]*", value) else json.dumps(value)


def _yaml(value: dict[str, Any], indent: int = 0) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for key, item in value.items():
        if isinstance(item, Block):
            lines.append(f"{pad}{_key(key)}: |")
            lines += [f"{pad}  {line}" if line else "" for line in item.splitlines()]
        elif isinstance(item, dict) and item:
            lines.append(f"{pad}{_key(key)}:")
            lines += _yaml(item, indent + 2)
        elif isinstance(item, list) and item:
            lines.append(f"{pad}{_key(key)}:")
            lines += [f"{pad}  - {_scalar(element)}" for element in item]
        else:
            lines.append(f"{pad}{_key(key)}: {_scalar(item)}")
    return lines


def _frontmatter(data: dict[str, Any], body: str, head: list[str] | None = None) -> str:
    lines = ["---", *(head or []), *_yaml(data), "---", ""]
    return "\n".join(lines) + "\n" + body


def _script_config(tool: dict, source_path: str, timeout: int) -> dict[str, Any]:
    inputs = {}
    for name, schema in tool["schema"]["properties"].items():
        entry: dict[str, Any] = {
            # mcp-scripts documents string/number/boolean inputs; integers travel as numbers.
            "type": "number" if schema["type"] == "integer" else schema["type"],
            "required": name in tool["schema"]["required"],
        }
        for field in ("description", "enum", "default"):
            if field in schema:
                entry[field] = schema[field]
        inputs[name] = entry
    stubs = sorted(tool["framework_imports"])
    config: dict[str, Any] = {"description": tool["description"]}
    if inputs:
        config["inputs"] = inputs
    if tool["dependencies"]:
        config["dependencies"] = tool["dependencies"]
    script = SCRIPT_IMPORTS + (FRAMEWORK_STUB.format(stubs=stubs) if stubs else "")
    script += SCRIPT_CALL.format(source_path=source_path, module=tool["module"], function=tool["function"])
    config["py"] = Block(script)
    if tool["secrets"]:
        config["env"] = {secret: f"${{{{ secrets.{secret} }}}}" for secret in tool["secrets"]}
    config["timeout"] = timeout
    return config


def _shared(portable: list[dict], data: dict, timeouts: dict[str, int], default_timeout: int) -> str:
    scripts = {tool["name"]: _script_config(tool, data["source_path"], timeouts.get(tool["name"], default_timeout))
               for tool in portable}
    names = ", ".join(f"`{tool['name']}`" for tool in portable) or "none"
    body = (f"Read-only MCP tools compiled by mcp-compile from `{data['source_path']}`: {names}.\n"
            "They run on the Actions runner outside the agent sandbox and must never mutate state.\n")
    return _frontmatter({"mcp-scripts": scripts}, body)


def _workflow(portable: list[dict], excluded: list[dict], workflow_name: str, shared_file: str,
              engine: str, trigger: str) -> str:
    lines = [f"# {workflow_name}", ""]
    if portable:
        lines += ["Use these read-only tools when their results are needed:", ""]
        lines += [f"- `{tool['name']}`: {tool['description'].splitlines()[0]}" for tool in portable]
    else:
        lines.append("No portable tools were found; this workflow exposes no generated tools.")
    mutating = [tool for tool in excluded if not tool["read_only"]]
    if mutating:
        lines += ["", "These tools were excluded because they mutate state. Request those changes through safe outputs instead:", ""]
        lines += [f"- `{tool['name']}`" for tool in mutating]
    return _frontmatter(
        {"engine": engine, "imports": [shared_file]},
        "\n".join(lines) + "\n",
        # `on` is written verbatim so callers can pass gh-aw trigger shorthands.
        head=[f"name: {_scalar(f'GitHub Agentic Workflow - {workflow_name}')}", f"on: {trigger}"],
    )


def _report(data: dict) -> str:
    tools = data["tools"]
    portable = [tool for tool in tools if tool["classification"] == "portable"]
    lines = ["# MCP Compile Report", "", f"Source: `{data['repository']}/{data['source_path']}`", "",
             "## Tool matrix", "", "| Tool | Classification | Read-only | Secrets | Dependencies | Generated |",
             "|---|---|---|---|---|---|"]
    for tool in tools:
        lines.append(
            f"| `{tool['name']}` | {tool['classification']} | {'yes' if tool['read_only'] else 'no'} | "
            f"{', '.join(tool['secrets']) or 'none'} | {', '.join(tool['dependencies']) or 'none'} | "
            f"{'yes' if tool in portable else 'no'} |")
    lines += ["", "## Findings", ""]
    for tool in tools:
        lines.append(f"### `{tool['name']}` ({tool['source_file']}:{tool['line']})")
        lines += [""] + [f"- {reason}" for reason in tool["reasons"]] + [f"- warning: {warning}" for warning in tool["warnings"]] + [""]
    lines += ["## Runtime model", "",
              f"- Portable tools generated: **{len(portable)} / {len(tools)}**.",
              "- Each call starts a fresh Python process on the Actions runner, so there is no warm state and each call pays interpreter and import start-up time.",
              "- Runner minutes are billed for the whole workflow run, not per tool call. Measure real runs before comparing costs with a hosted server.",
              f"- Outputs over {LARGE_OUTPUT_CHARACTERS} characters are saved to a file; the agent receives its path, size and schema preview.",
              "- Mutating tools are never generated: route them through Safe Outputs or Custom Safe Output Jobs.",
              "", "## Limits of the analysis", "",
              "- Read-only detection is static and follows same-module helpers only. Code in other modules, dynamic dispatch and third-party libraries are not inspected.",
              "- Declare `annotations={\"readOnlyHint\": True}` on read-only tools so intent is explicit and checked against the code."]
    return "\n".join(lines) + "\n"


def _security(portable: list[dict]) -> str:
    lines = ["# Security review", "",
             "Generated tools run on the GitHub Actions runner, outside the agent sandbox, and must stay read-only.", ""]
    with_secrets = [tool for tool in portable if tool["secrets"]]
    if with_secrets:
        lines += ["## Secrets to map", "", "| Tool | Secrets |", "|---|---|"]
        lines += [f"| `{tool['name']}` | {', '.join(tool['secrets'])} |" for tool in with_secrets]
        lines.append("")
    lines += ["## Checklist", "",
              "- Map only the secrets listed above, preferably in a protected environment.",
              "- Never expose secrets to workflows triggered by forked pull requests.",
              "- Review every pinned dependency before allowing it to be installed on the runner.",
              "- Re-run `mcp-compile compile` after changing the server so the read-only analysis stays current."]
    return "\n".join(lines) + "\n"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "mcp-tools"


def _prepare(destination: Path, source_root: Path) -> None:
    if destination == source_root or destination in source_root.parents:
        raise ValueError(f"refusing to overwrite {destination}: it contains the source repository")
    if destination.exists():
        if not destination.is_dir():
            raise ValueError(f"{destination} exists and is not a directory")
        if any(destination.iterdir()) and not (destination / MARKER).exists():
            raise ValueError(f"refusing to overwrite {destination}: it is not empty and was not created by mcp-compile")
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    (destination / MARKER).write_text("Generated by mcp-compile; this directory is replaced on every compile.\n", encoding="utf-8")


def generate(repository: str | Path, output: str | Path, name: str | None = None, *, engine: str = "copilot",
             trigger: str = "workflow_dispatch", timeout: int = DEFAULT_TIMEOUT,
             tool_timeouts: dict[str, int] | None = None) -> dict:
    source_root = Path(repository).resolve()
    destination = Path(output).resolve()
    workflow_name = name or source_root.name
    workflow_slug = _slug(workflow_name)
    data = manifest(source_root)
    unknown = set(tool_timeouts or {}) - {tool["name"] for tool in data["tools"]}
    if unknown:
        raise ValueError(f"unknown tool(s) in --tool-timeout: {', '.join(sorted(unknown))}")
    _prepare(destination, source_root)
    portable = [tool for tool in data["tools"] if tool["classification"] == "portable"]
    excluded = [tool for tool in data["tools"] if tool["classification"] != "portable"]
    data["generated_tools"] = [tool["name"] for tool in portable]
    (destination / "manifest.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    workflows = destination / ".github" / "workflows"
    (workflows / "shared").mkdir(parents=True)
    shared_file = f"shared/{workflow_slug}-tools.md"
    (workflows / shared_file).write_text(_shared(portable, data, tool_timeouts or {}, timeout), encoding="utf-8")
    (workflows / f"{workflow_slug}.md").write_text(
        _workflow(portable, excluded, workflow_name, shared_file, engine, trigger), encoding="utf-8")
    (destination / "REPORT.md").write_text(_report(data), encoding="utf-8")
    (destination / "SECURITY.md").write_text(_security(portable), encoding="utf-8")
    tests = destination / "tests"
    tests.mkdir()
    (tests / "test_contracts.py").write_text(
        _contract_tests(portable, source_root, repository_root(source_root), shared_file), encoding="utf-8")
    return data


def _sample(schema: dict[str, Any]) -> Any:
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    return SAMPLE_VALUES.get(schema["type"])


def _contract_tests(portable: list[dict], source_root: Path, checkout: Path, shared_file: str) -> str:
    cases = {tool["name"]: {name: _sample(schema) for name, schema in tool["schema"]["properties"].items()}
             for tool in portable}
    functions = {tool["name"]: [tool["module"], tool["function"]] for tool in portable}
    secrets = sorted({secret for tool in portable for secret in tool["secrets"]})
    return f'''"""Contract tests: each generated mcp-scripts `py:` block must match the source tool."""
import asyncio
import importlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

BUNDLE = Path(__file__).parents[1]
SHARED = BUNDLE / ".github" / "workflows" / {shared_file!r}
SOURCE_ROOT = {str(source_root)!r}
CHECKOUT = {str(checkout)!r}
CASES = {cases!r}
FUNCTIONS = {functions!r}
SECRETS = {secrets!r}
# Mirrors gh-aw: the script runs with a predefined `inputs` dictionary.
HARNESS = "import json, sys; inputs = json.load(sys.stdin); exec(compile(sys.argv[1], 'mcp-script', 'exec'), {{'inputs': inputs, '__name__': '__main__'}})"


def _environment():
    environment = {{key: value for key, value in os.environ.items() if key != "GITHUB_WORKSPACE"}}
    environment.update({{secret: "contract-test" for secret in SECRETS}})
    return environment


def _scripts():
    frontmatter = SHARED.read_text(encoding="utf-8").split("---")[1]
    return yaml.safe_load(frontmatter)["mcp-scripts"]


def test_shared_workflow_matches_manifest():
    data = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    scripts = _scripts() or {{}}
    assert set(scripts) == set(data["generated_tools"])
    for name, script in scripts.items():
        assert script["description"]
        assert set(script) - {{"description", "inputs", "dependencies", "py", "env", "timeout"}} == set()
        assert "env" not in script or script["env"], name


@pytest.mark.parametrize("name", sorted(CASES))
def test_generated_script_matches_source(name, monkeypatch):
    for secret in SECRETS:
        monkeypatch.setenv(secret, "contract-test")
    sys.path.insert(0, SOURCE_ROOT)
    module, function = FUNCTIONS[name]
    original = getattr(importlib.import_module(module), function)
    original = getattr(original, "fn", original)
    expected = original(**CASES[name])
    if inspect.isawaitable(expected):
        expected = asyncio.run(expected)
    result = subprocess.run([sys.executable, "-c", HARNESS, _scripts()[name]["py"]], input=json.dumps(CASES[name]),
                            capture_output=True, text=True, cwd=CHECKOUT, env=_environment())
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == json.loads(json.dumps(expected, default=str))
'''
