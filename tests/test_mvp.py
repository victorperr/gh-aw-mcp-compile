import json
from pathlib import Path

from mcp_compiler.analyzer import manifest
from mcp_compiler.generator import generate


ROOT = Path(__file__).parents[1]
SAMPLE = ROOT / "examples" / "sample-server"


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
    workflow = (bundle / ".github" / "workflows" / "mcp-tools.md").read_text()
    assert "mcp-scripts:" in workflow
    assert "workflow_dispatch" in workflow
    assert "runner cost" in (bundle / "REPORT.md").read_text()
    json.loads((bundle / "manifest.json").read_text())


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
