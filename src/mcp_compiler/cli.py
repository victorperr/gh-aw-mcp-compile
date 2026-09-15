from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analyzer import manifest
from .generator import generate


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="mcp-compile", description="Compile FastMCP tools into secure ephemeral targets.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect", help="discover and classify tools")
    inspect_parser.add_argument("repository", type=Path)
    inspect_parser.add_argument("--json", action="store_true")
    compile_parser = subparsers.add_parser(
        "compile", help="generate GitHub-native artifacts")
    compile_parser.add_argument("repository", type=Path)
    compile_parser.add_argument("--out", type=Path, default=Path(".mcp-build"))
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
                print(f"  secrets: {', '.join(tool['secrets']) or 'none'}")
                print(f"  reason:  {'; '.join(tool['reasons'])}")
        return 0
    if args.command == "compile":
        data = generate(args.repository, args.out)
        print(
            f"Generated {len(data['generated_tools'])} portable tool(s) in {args.out}")
        print(f"Report: {args.out / 'REPORT.md'}")
        return 0
    if args.command == "contract":
        import pytest
        return pytest.main([str(args.bundle / "tests" / "test_contracts.py"), "-q"])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
