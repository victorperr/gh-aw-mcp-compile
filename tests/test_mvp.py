import json
from pathlib import Path

from mcp_compiler.analyzer import manifest
from mcp_compiler.generator import generate


ROOT = Path(__file__).parents[1]
SAMPLE = ROOT / "examples" / "sample-server"


def test_sample_is_a_real_fastmcp_server():
    source = (SAMPLE / "server.py").read_text()
    assert "from fastmcp import FastMCP" in source
    assert "mcp = FastMCP(" in source
    assert "class _MCP" not in source


def test_sample_is_classified_and_generated(tmp_path):
    data = manifest(SAMPLE)
    by_name = {tool["name"]: tool for tool in data["tools"]}
    assert by_name["greet"]["classification"] == "portable"
    assert by_name["read_account"]["classification"] == "partially portable"
    assert by_name["read_account"]["secrets"] == ["ACCOUNTS_API_KEY"]
    assert by_name["save_report"]["classification"] == "partially portable"
    bundle = tmp_path / "bundle"
    generated = generate(SAMPLE, bundle)
    assert generated["generated_tools"] == ["greet"]
    assert (bundle / "tools" / "greet.py").exists()
    workflow_path = bundle / ".github" / "workflows" / "sample-server.md"
    workflow = workflow_path.read_text()
    assert 'name: "GitHub Agentic Workflow - sample-server"' in workflow
    assert "mcp-scripts:" in workflow
    assert 'sys.path.insert(0, "examples/sample-server")' in workflow
    assert "workflow_dispatch" in workflow
    assert "runner cost" in (bundle / "REPORT.md").read_text()
    json.loads((bundle / "manifest.json").read_text())


def test_workflow_name_and_filename_can_be_customized(tmp_path):
    bundle = tmp_path / "bundle"
    generate(SAMPLE, bundle, name="Finance CRM")

    workflow_path = bundle / ".github" / "workflows" / "finance-crm.md"
    assert workflow_path.exists()
    assert 'name: "GitHub Agentic Workflow - Finance CRM"' in workflow_path.read_text()


def test_generated_contract_is_executable(tmp_path):
    bundle = tmp_path / "bundle"
    generate(SAMPLE, bundle)

    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
            str(bundle / "tests" / "test_contracts.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
