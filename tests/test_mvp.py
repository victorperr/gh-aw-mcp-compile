import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from mcp_compiler.analyzer import manifest
from mcp_compiler.generator import generate


ROOT = Path(__file__).parents[1]
SAMPLE = ROOT / "examples" / "sample-server"


def _tools(tmp_path, source):
    server = tmp_path / "server"
    server.mkdir(exist_ok=True)
    (server / "server.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return {tool["name"]: tool for tool in manifest(server)["tools"]}


def _frontmatter(path):
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])


def test_sample_is_a_real_fastmcp_server():
    source = (SAMPLE / "server.py").read_text()
    assert "from fastmcp import FastMCP" in source
    assert "mcp = FastMCP(" in source
    assert "class _MCP" not in source


def test_sample_is_classified():
    by_name = {tool["name"]: tool for tool in manifest(SAMPLE)["tools"]}
    assert by_name["greet"]["classification"] == "portable"
    assert by_name["read_account"]["classification"] == "portable"
    assert by_name["read_account"]["secrets"] == ["ACCOUNTS_API_KEY"]
    assert by_name["text_stats"]["classification"] == "portable"
    assert by_name["text_stats"]["is_async"]
    assert by_name["save_report"]["classification"] == "incompatible"
    assert not by_name["save_report"]["read_only"]


def test_sample_is_generated(tmp_path):
    bundle = tmp_path / "bundle"
    generated = generate(SAMPLE, bundle)
    assert generated["generated_tools"] == ["greet", "read_account", "text_stats"]
    assert generated["source_path"] == "examples/sample-server"
    assert "C:" not in json.dumps(generated) and str(ROOT) not in json.dumps(generated)

    workflow = _frontmatter(bundle / ".github" / "workflows" / "sample-server.md")
    assert workflow["name"] == "GitHub Agentic Workflow - sample-server"
    assert workflow["imports"] == ["shared/sample-server-tools.md"]

    scripts = _frontmatter(bundle / ".github" / "workflows" / "shared" / "sample-server-tools.md")["mcp-scripts"]
    assert set(scripts) == {"greet", "read_account", "text_stats"}
    assert "env" not in scripts["greet"]
    assert scripts["read_account"]["env"] == {"ACCOUNTS_API_KEY": "${{ secrets.ACCOUNTS_API_KEY }}"}
    assert scripts["read_account"]["inputs"]["account_id"]["description"] == "Identifier of the account to read."
    assert scripts["text_stats"]["inputs"]["unit"] == {
        "type": "string", "required": False, "enum": ["words", "characters"], "default": "words"}
    for script in scripts.values():
        compile(script["py"], "mcp-script", "exec")
    assert "safe outputs" in (bundle / "REPORT.md").read_text()


def test_workflow_options_can_be_customized(tmp_path):
    bundle = tmp_path / "bundle"
    generate(SAMPLE, bundle, name="Finance CRM", engine="claude", trigger="push", timeout=90,
             tool_timeouts={"greet": 300})

    workflow_path = bundle / ".github" / "workflows" / "finance-crm.md"
    workflow = workflow_path.read_text()
    assert 'name: "GitHub Agentic Workflow - Finance CRM"' in workflow
    assert "on: push" in workflow and "engine: claude" in workflow
    scripts = _frontmatter(bundle / ".github" / "workflows" / "shared" / "finance-crm-tools.md")["mcp-scripts"]
    assert scripts["greet"]["timeout"] == 300
    assert scripts["read_account"]["timeout"] == 90


def test_generated_contract_is_executable(tmp_path):
    bundle = tmp_path / "bundle"
    generate(SAMPLE, bundle)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(bundle / "tests" / "test_contracts.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 passed" in result.stdout


def test_output_directory_is_protected(tmp_path):
    with pytest.raises(ValueError, match="contains the source"):
        generate(SAMPLE, SAMPLE)
    with pytest.raises(ValueError, match="contains the source"):
        generate(SAMPLE, ROOT)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("user data")
    with pytest.raises(ValueError, match="not created by mcp-compile"):
        generate(SAMPLE, occupied)
    assert (occupied / "keep.txt").exists()

    bundle = tmp_path / "bundle"
    generate(SAMPLE, bundle)
    generate(SAMPLE, bundle)  # a previous bundle can be regenerated


def test_mutations_are_detected(tmp_path):
    tools = _tools(tmp_path, """
        import os, shutil, sqlite3, subprocess
        from pathlib import Path
        import urllib.request as requests
        from fastmcp import FastMCP

        app = FastMCP("x")

        def _persist(text):
            Path("out.txt").write_text(text)

        @app.tool
        def post(url: str) -> str:
            return requests.post(url, json={}).text

        @app.tool
        def via_helper(text: str) -> None:
            _persist(text)

        @app.tool
        def sql(user: str) -> None:
            sqlite3.connect("db").execute(f"DELETE FROM users WHERE name = '{user}'")

        @app.tool
        def remove(path: str) -> None:
            shutil.rmtree(path)

        @app.tool
        def run(command: str) -> str:
            return subprocess.check_output(command)

        @app.tool(annotations={"readOnlyHint": False})
        def declared() -> None:
            pass

        @app.tool
        def reads(path: str) -> str:
            with open(path, encoding="utf-8") as handle:
                return handle.read().replace("a", "b")
    """)
    for name in ("post", "via_helper", "sql", "remove", "run", "declared"):
        assert tools[name]["classification"] == "incompatible", name
    assert not tools["post"]["read_only"]
    assert tools["reads"]["classification"] == "partially portable"
    assert tools["reads"]["read_only"]


def test_schema_features(tmp_path):
    tools = _tools(tmp_path, """
        import os
        from typing import Annotated, Literal, Optional
        from fastmcp import Context, FastMCP

        TOKEN = os.environ["SERVICE_TOKEN"]
        mcp = FastMCP("x")

        @mcp.tool(name="search-items", description="Search the catalog.")
        async def search(
            query: str | None,
            limit: int = 10,
            tags: list[str] = [],
            order: Literal["asc", "desc"] = "asc",
            note: Annotated[Optional[str], "Free text"] = None,
            ctx: Context = None,
        ) -> list[dict]:
            return [os.getenv("HOME")]
    """)
    tool = tools["search-items"]
    assert tool["function"] == "search"
    assert tool["description"] == "Search the catalog."
    assert tool["classification"] == "portable"
    assert tool["secrets"] == ["SERVICE_TOKEN"]
    properties = tool["schema"]["properties"]
    assert set(properties) == {"query", "limit", "tags", "order", "note"}
    assert properties["query"] == {"type": "string"}
    assert properties["limit"] == {"type": "integer", "default": 10}
    assert properties["tags"] == {"type": "array", "items": {"type": "string"}, "default": []}
    assert properties["order"] == {"type": "string", "enum": ["asc", "desc"], "default": "asc"}
    assert properties["note"] == {"type": "string", "description": "Free text"}
    assert tool["schema"]["required"] == ["query"]


def test_dependencies_are_pinned(tmp_path):
    tools = _tools(tmp_path, """
        import json
        import pytest
        import helpers
        import not_a_real_package_xyz
        from fastmcp import FastMCP

        mcp = FastMCP("x")

        @mcp.tool
        def tool() -> str:
            return "ok"
    """)
    (tmp_path / "server" / "helpers.py").write_text("", encoding="utf-8")
    tools = {tool["name"]: tool for tool in manifest(tmp_path / "server")["tools"]}
    assert tools["tool"]["dependencies"] == [f"pytest=={pytest.__version__}"]
    assert tools["tool"]["classification"] == "partially portable"
    assert any("not_a_real_package_xyz" in reason for reason in tools["tool"]["reasons"])
