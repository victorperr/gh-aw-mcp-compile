from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

TOOL_DECORATORS = {"tool", "mcp.tool", "server.tool"}
SECRET_PATTERNS = (
    re.compile(r"os\.getenv\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']"),
    re.compile(r"os\.environ(?:\.get)?\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']"),
)
JSON_TYPES = {"str": "string", "int": "integer",
              "float": "number", "bool": "boolean"}


@dataclass
class Tool:
    name: str
    source_file: str
    line: int
    description: str
    schema: dict[str, Any]
    secrets: list[str]
    classification: str
    reasons: list[str]
    permissions: dict[str, str]
    estimated_latency_ms: int
    estimated_cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _annotation(annotation: ast.expr | None) -> tuple[dict[str, Any], bool]:
    if annotation is None:
        return {"type": "string"}, False
    if isinstance(annotation, ast.Name) and annotation.id in JSON_TYPES:
        return {"type": JSON_TYPES[annotation.id]}, True
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return {"type": "null"}, True
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        container = annotation.value.id
        if container in {"list", "List"}:
            item, supported = _annotation(annotation.slice)
            return {"type": "array", "items": item}, supported
        if container in {"dict", "Dict"}:
            return {"type": "object"}, True
        if container in {"Optional", "Union"}:
            args = annotation.slice.elts if isinstance(
                annotation.slice, ast.Tuple) else [annotation.slice]
            non_null = [item for item in args if not (
                isinstance(item, ast.Constant) and item.value is None)]
            if len(non_null) == 1:
                schema, supported = _annotation(non_null[0])
                return schema, supported
    return {"type": "string"}, False


def _decorator_name(decorator: ast.expr) -> str:
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        parent = _decorator_name(decorator.value)
        return f"{parent}.{decorator.attr}" if parent else decorator.attr
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
    return ""


def _secrets(node: ast.AST) -> list[str]:
    source = ast.unparse(node)
    found = {match.group(
        1) for pattern in SECRET_PATTERNS for match in pattern.finditer(source)}
    return sorted(found)


def _tool_from_node(node: ast.FunctionDef | ast.AsyncFunctionDef, relative_file: str, tree: ast.AST) -> Tool:
    properties: dict[str, Any] = {}
    required: list[str] = []
    supported = True
    positional = list(node.args.posonlyargs) + list(node.args.args)
    defaults = [None] * (len(positional) -
                         len(node.args.defaults)) + list(node.args.defaults)
    for argument, default in zip(positional, defaults):
        schema, argument_supported = _annotation(argument.annotation)
        supported = supported and argument_supported
        properties[argument.arg] = schema
        if default is None:
            required.append(argument.arg)
    if node.args.kwonlyargs:
        for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
            schema, argument_supported = _annotation(argument.annotation)
            supported = supported and argument_supported
            properties[argument.arg] = schema
            if default is None:
                required.append(argument.arg)
    reasons: list[str] = []
    function_source = ast.unparse(node)
    if isinstance(node, ast.AsyncFunctionDef):
        reasons.append("async functions need a persistent async runtime")
    if re.search(r"\b(open|Path)\s*\(", function_source) or "subprocess" in function_source:
        reasons.append(
            "filesystem or subprocess access requires explicit sandbox policy")
    if re.search(r"\b(global|nonlocal)\b", function_source):
        reasons.append(
            "mutable shared state is not preserved between invocations")
    if not supported:
        reasons.append(
            "one or more annotations are outside the JSON schema subset")
    secrets = _secrets(node)
    if secrets:
        reasons.append("secrets require explicit GitHub environment mapping")
    if reasons and any("need a persistent" in reason or "outside" in reason for reason in reasons):
        classification = "incompatible"
    elif reasons:
        classification = "partially portable"
    else:
        classification = "portable"
    return Tool(
        name=node.name,
        source_file=relative_file,
        line=node.lineno,
        description=ast.get_docstring(node) or "No description provided.",
        schema={"type": "object", "properties": properties, "required": required},
        secrets=secrets,
        classification=classification,
        reasons=reasons or ["JSON-compatible synchronous function"],
        permissions={"contents": "read"} if not any(word in function_source for word in (
            "write", "create", "delete", "update")) else {"contents": "write"},
        estimated_latency_ms=1200 if secrets else 800,
        estimated_cost_usd=0.008,
    )


def discover(repository: str | Path) -> list[Tool]:
    root = Path(repository).resolve()
    tools: list[Tool] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in {".venv", "venv", "__pycache__", ".git"} for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(_decorator_name(decorator) in TOOL_DECORATORS for decorator in node.decorator_list):
                tools.append(_tool_from_node(
                    node, path.relative_to(root).as_posix(), tree))
    return tools


def manifest(repository: str | Path) -> dict[str, Any]:
    tools = discover(repository)
    return {"compiler_version": "0.1.0", "repository": str(Path(repository).resolve()), "tools": [tool.to_dict() for tool in tools]}


def manifest_json(repository: str | Path) -> str:
    return json.dumps(manifest(repository), indent=2)
