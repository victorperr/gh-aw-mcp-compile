from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analyzer import manifest
from .generator import DEFAULT_TIMEOUT, generate


def _tool_timeout(value: str) -> tuple[str, int]:
    name, separator, seconds = value.partition("=")
    if not separator or not name or not seconds.isdigit() or int(seconds) <= 0:
        raise argparse.ArgumentTypeError("expected TOOL=SECONDS, for example slow_report=300")
    return name, int(seconds)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="mcp-compile", description="Compile FastMCP tools into GitHub Agentic Workflows mcp-scripts.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect", help="discover and classify tools")
    inspect_parser.add_argument("repository", type=Path)
    inspect_parser.add_argument("--json", action="store_true")
    compile_parser = subparsers.add_parser(
        "compile", help="generate GitHub-native artifacts")
    compile_parser.add_argument("repository", type=Path)
    compile_parser.add_argument("--out", type=Path, default=Path(".mcp-build"))
    compile_parser.add_argument(
        "--name", help="workflow display name (defaults to the repository folder name)")
    compile_parser.add_argument("--engine", default="copilot", help="agentic engine (default: copilot)")
    compile_parser.add_argument("--on", dest="trigger", default="workflow_dispatch",
                                help="workflow trigger written to `on:` (default: workflow_dispatch)")
    compile_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                                help=f"default tool timeout in seconds (default: {DEFAULT_TIMEOUT})")
    compile_parser.add_argument("--tool-timeout", type=_tool_timeout, action="append", default=[],
                                metavar="TOOL=SECONDS", help="per-tool timeout override, repeatable")
    contract_parser = subparsers.add_parser(
        "contract", help="run generated contract tests")
    contract_parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    if args.command == "inspect":
        data = manifest(args.repository)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            for tool in data["tools"]:
                print(
                    f"{tool['classification']:18} {tool['name']:24} {tool['source_file']}:{tool['line']}")
                print(f"  read-only:    {'yes' if tool['read_only'] else 'no'}")
                print(f"  secrets:      {', '.join(tool['secrets']) or 'none'}")
                print(f"  dependencies: {', '.join(tool['dependencies']) or 'none'}")
                for reason in tool["reasons"]:
                    print(f"  reason:       {reason}")
                for warning in tool["warnings"]:
                    print(f"  warning:      {warning}")
        return 0
    if args.command == "compile":
        try:
            data = generate(args.repository, args.out, args.name, engine=args.engine, trigger=args.trigger,
                            timeout=args.timeout, tool_timeouts=dict(args.tool_timeout))
        except ValueError as error:
            print(f"mcp-compile: error: {error}", file=sys.stderr)
            return 2
        print(
            f"Generated {len(data['generated_tools'])} portable tool(s) in {args.out}")
        print(f"Workflows: {args.out / '.github' / 'workflows'}")
        print(f"Report: {args.out / 'REPORT.md'}")
        return 0
    if args.command == "contract":
        import pytest
        return pytest.main([str(args.bundle / "tests" / "test_contracts.py"), "-q"])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
