from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

from . import __version__

SERVER_CLASSES = {"FastMCP"}
DEFAULT_SERVER_NAMES = {"mcp", "server"}
FRAMEWORK_PACKAGES = {"fastmcp", "mcp"}
SKIPPED_DIRECTORIES = {"venv", "env", "__pycache__", "node_modules", "build", "dist", "site-packages"}
JSON_TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}
ARRAY_TYPES = {"list", "List", "Sequence", "set", "Set", "tuple", "Tuple"}
OBJECT_TYPES = {"dict", "Dict", "Mapping"}
# Runner-provided variables are configuration, not secrets to map from `secrets.*`.
RUNNER_VARIABLES = {"HOME", "PATH", "USER", "PWD", "TMPDIR", "TEMP", "TMP", "LANG", "CI", "SHELL"}

WRITE_MODE = re.compile(r"[wax+]")
PATH_WRITE_METHODS = {"write_text", "write_bytes", "unlink", "rmdir", "mkdir", "touch", "chmod", "symlink_to", "hardlink_to"}
OS_WRITE_FUNCTIONS = {"remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs", "rename", "renames",
                      "replace", "chmod", "chown", "symlink", "link", "truncate", "putenv", "unsetenv"}
PROCESS_FUNCTIONS = {"os.system", "os.popen", "os.spawnl", "os.spawnv", "os.execv", "os.execvp", "os.fork"}
SHUTIL_READ_FUNCTIONS = {"shutil.which", "shutil.disk_usage", "shutil.get_terminal_size"}
HTTP_WRITE_METHODS = {"post", "put", "patch", "delete"}
SQL_CALLS = {"execute", "executemany", "executescript", "exec_driver_sql", "text"}
SQL_WRITE = re.compile(r"^\s*(insert|update|delete|drop|alter|create|truncate|replace|merge|grant|revoke)\b", re.I)
FS_READ_CALLS = {"os.listdir", "os.scandir", "os.walk", "glob.glob", "glob.iglob"}
FS_READ_METHODS = {"read_text", "read_bytes", "iterdir", "glob", "rglob"}


@dataclass
class Tool:
    name: str
    function: str
    source_file: str
    module: str
    line: int
    description: str
    schema: dict[str, Any]
    returns: str | None
    is_async: bool
    read_only: bool
    mutations: list[str]
    secrets: list[str]
    dependencies: list[str]
    framework_imports: list[str]
    classification: str
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _constant(node: ast.expr | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((keyword.value for keyword in call.keywords if keyword.arg == name), None)


def _is_context(annotation: ast.expr | None) -> bool:
    return annotation is not None and _dotted(annotation).split(".")[-1] == "Context"


def _union_members(annotation: ast.expr) -> list[ast.expr] | None:
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        return (_union_members(annotation.left) or [annotation.left]) + (_union_members(annotation.right) or [annotation.right])
    if isinstance(annotation, ast.Subscript) and _dotted(annotation.value).split(".")[-1] in {"Optional", "Union"}:
        elements = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
        if _dotted(annotation.value).endswith("Optional"):
            return elements + [ast.Constant(value=None)]
        return list(elements)
    return None


def _annotation(annotation: ast.expr | None) -> tuple[dict[str, Any], bool]:
    """Translate a Python annotation into the JSON schema subset supported by mcp-scripts inputs."""
    if annotation is None:
        return {"type": "string"}, False
    members = _union_members(annotation)
    if members is not None:
        non_null = [item for item in members if not (isinstance(item, ast.Constant) and item.value is None)]
        if len(non_null) == 1:
            return _annotation(non_null[0])
        return {"type": "string"}, False
    if isinstance(annotation, ast.Name) and annotation.id in JSON_TYPES:
        return {"type": JSON_TYPES[annotation.id]}, True
    if isinstance(annotation, ast.Name) and annotation.id in ARRAY_TYPES:
        return {"type": "array"}, True
    if isinstance(annotation, ast.Name) and annotation.id in OBJECT_TYPES:
        return {"type": "object"}, True
    if isinstance(annotation, ast.Subscript):
        container = _dotted(annotation.value).split(".")[-1]
        arguments = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
        if container == "Annotated":
            schema, supported = _annotation(arguments[0])
            for extra in arguments[1:]:
                if isinstance(extra, ast.Constant) and isinstance(extra.value, str):
                    schema["description"] = extra.value
                elif isinstance(extra, ast.Call) and _dotted(extra.func).endswith("Field"):
                    description = _constant(_keyword(extra, "description"))
                    if isinstance(description, str):
                        schema["description"] = description
            return schema, supported
        if container == "Literal":
            values = [_constant(item) for item in arguments]
            kinds = {type(value).__name__ for value in values}
            if len(kinds) == 1 and next(iter(kinds)) in JSON_TYPES:
                return {"type": JSON_TYPES[next(iter(kinds))], "enum": values}, True
            return {"type": "string"}, False
        if container in ARRAY_TYPES:
            item, supported = _annotation(arguments[0])
            return {"type": "array", "items": item}, supported
        if container in OBJECT_TYPES:
            return {"type": "object"}, True
    return {"type": "string"}, False


def _parameter(argument: ast.arg, default: ast.expr | None) -> tuple[dict[str, Any], bool, bool]:
    """Return the input schema, whether its annotation is supported, and whether it is required."""
    schema, supported = _annotation(argument.annotation)
    if default is None:
        return schema, supported, True
    if isinstance(default, ast.Call) and _dotted(default.func).endswith("Field"):
        description = _constant(_keyword(default, "description"))
        if isinstance(description, str):
            schema["description"] = description
        default = _keyword(default, "default") or (default.args[0] if default.args else None)
        if default is None or (isinstance(default, ast.Constant) and default.value is Ellipsis):
            return schema, supported, True
    value = _constant(default)
    if value is not None:
        schema["default"] = value
    return schema, supported, False


def _docstring(node: ast.AST) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into its summary and per-argument descriptions."""
    text = ast.get_docstring(node) or ""
    header = re.search(r"^(?:Args|Arguments|Parameters):\s*$", text, flags=re.M)
    summary, rest = (text[:header.start()], text[header.end():]) if header else (text, "")
    arguments: dict[str, str] = {}
    current = None
    for line in rest.splitlines():
        if re.match(r"^\S", line):
            break
        match = re.match(r"^\s+(\w+)(?:\s*\([^)]*\))?:\s*(.*)$", line)
        if match:
            current = match.group(1)
            arguments[current] = match.group(2).strip()
        elif current and line.strip():
            arguments[current] = f"{arguments[current]} {line.strip()}"
    return summary.strip(), arguments


class _Effects(ast.NodeVisitor):
    """Collect side effects, secrets and helper calls from a function body."""

    def __init__(self) -> None:
        self.mutations: set[str] = set()
        self.processes: set[str] = set()
        self.file_reads: set[str] = set()
        self.secrets: set[str] = set()
        self.shared_state = False
        self.helpers: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        self.shared_state = True

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.shared_state = True

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _dotted(node.value) in {"os.environ", "environ"}:
            self._secret(node.slice)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted(node.func)
        method = name.split(".")[-1]
        if isinstance(node.func, ast.Name):
            self.helpers.add(node.func.id)
        if name in {"os.getenv", "getenv", "os.environ.get", "environ.get"} and node.args:
            self._secret(node.args[0])
        if method == "open" and name not in {"webbrowser.open"}:
            self._open(node, name)
        elif name.startswith("subprocess.") or name in PROCESS_FUNCTIONS or name.startswith("asyncio.create_subprocess"):
            self.processes.add(f"starts a subprocess via {name}()")
        elif name.startswith("shutil.") and name not in SHUTIL_READ_FUNCTIONS:
            self.mutations.add(f"modifies files via {name}()")
        elif name.startswith("os.") and method in OS_WRITE_FUNCTIONS:
            self.mutations.add(f"modifies the environment or files via {name}()")
        elif isinstance(node.func, ast.Attribute) and method in PATH_WRITE_METHODS:
            self.mutations.add(f"modifies files via .{method}()")
        elif isinstance(node.func, ast.Attribute) and method in HTTP_WRITE_METHODS:
            self.mutations.add(f"sends an HTTP {method.upper()} via {name}()")
        elif method == "request" and node.args and isinstance(_constant(node.args[0]), str) \
                and _constant(node.args[0]).lower() in HTTP_WRITE_METHODS:
            self.mutations.add(f"sends an HTTP {_constant(node.args[0]).upper()} via {name}()")
        elif method == "Request" and isinstance(_constant(_keyword(node, "method")), str) \
                and _constant(_keyword(node, "method")).lower() in HTTP_WRITE_METHODS:
            self.mutations.add(f"sends an HTTP {_constant(_keyword(node, 'method')).upper()} via {name}()")
        elif method in SQL_CALLS and node.args and SQL_WRITE.match(self._text(node.args[0])):
            self.mutations.add(f"runs a mutating SQL statement via {name}()")
        elif name in FS_READ_CALLS or (isinstance(node.func, ast.Attribute) and method in FS_READ_METHODS):
            self.file_reads.add(f"reads the runner filesystem via {name}()")
        self.generic_visit(node)

    def _open(self, node: ast.Call, name: str) -> None:
        # Built-in open() takes the mode second; Path.open() takes it first.
        builtin = name in {"open", "io.open", "codecs.open"}
        position = 1 if builtin else 0
        mode_node = _keyword(node, "mode") or (node.args[position] if len(node.args) > position else None)
        mode = "r" if mode_node is None else _constant(mode_node)
        if not builtin and not (isinstance(mode, str) and re.fullmatch(r"[rwaxbt+]{1,3}", mode)):
            mode = "r"  # e.g. Image.open(path): the first argument is not a mode
        if not isinstance(mode, str):
            self.mutations.add(f"opens a file with a dynamic mode via {name}()")
        elif WRITE_MODE.search(mode):
            self.mutations.add(f"writes files via {name}(mode={mode!r})")
        else:
            self.file_reads.add(f"reads the runner filesystem via {name}()")

    def _secret(self, node: ast.expr) -> None:
        value = _constant(node)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) and value not in RUNNER_VARIABLES \
                and not (value.startswith(("GITHUB_", "RUNNER_")) and value != "GITHUB_TOKEN"):
            self.secrets.add(value)

    @staticmethod
    def _text(node: ast.expr) -> str:
        if isinstance(node, ast.JoinedStr):
            return "".join(str(part.value) for part in node.values if isinstance(part, ast.Constant))
        value = _constant(node)
        return value if isinstance(value, str) else ""


def _effects(node: ast.AST, helpers: dict[str, ast.AST]) -> _Effects:
    """Analyze a function and every same-module helper it calls, transitively."""
    effects = _Effects()
    pending, seen = [node], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        effects.visit(current)
        pending += [helpers[name] for name in effects.helpers if name in helpers]
    return effects


def _module_secrets(tree: ast.Module) -> set[str]:
    effects = _Effects()
    for statement in tree.body:
        if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            effects.visit(statement)
    return effects.secrets


@lru_cache(maxsize=None)
def _distributions() -> dict[str, list[str]]:
    return metadata.packages_distributions()


def _dependencies(tree: ast.Module, root: Path, module_dir: Path) -> tuple[list[str], list[str], list[str]]:
    """Return pinned third-party requirements, unresolved imports, and MCP framework imports."""
    imported: set[str] = set()
    frameworks: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            if top in FRAMEWORK_PACKAGES:
                frameworks.add(name)
            else:
                imported.add(top)
    pinned, unresolved = set(), set()
    for top in imported:
        local = any((base / f"{top}.py").exists() or (base / top).is_dir() for base in {root, module_dir})
        if top == "__future__" or top in sys.stdlib_module_names or local:
            continue
        distributions = _distributions().get(top)
        if not distributions:
            unresolved.add(top)
            continue
        for distribution in distributions:
            pinned.add(f"{distribution}=={metadata.version(distribution)}")
    return sorted(pinned), sorted(unresolved), sorted(frameworks)


def _servers(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call) \
                and _dotted(node.value.func).split(".")[-1] in SERVER_CLASSES:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names |= {target.id for target in targets if isinstance(target, ast.Name)}
    return names or DEFAULT_SERVER_NAMES


def _tool_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef, servers: set[str]) -> ast.expr | None:
    accepted = {"tool"} | {f"{server}.tool" for server in servers}
    return next((decorator for decorator in node.decorator_list if _dotted(decorator) in accepted), None)


def _hints(decorator: ast.expr) -> tuple[str | None, str | None, dict[str, Any]]:
    """Read the name, description and MCP tool annotations from @mcp.tool(...)."""
    if not isinstance(decorator, ast.Call):
        return None, None, {}
    name = _constant(_keyword(decorator, "name")) or (_constant(decorator.args[0]) if decorator.args else None)
    description = _constant(_keyword(decorator, "description"))
    annotations = _keyword(decorator, "annotations")
    hints: dict[str, Any] = {}
    if isinstance(annotations, ast.Dict):
        hints = {_constant(key): _constant(value) for key, value in zip(annotations.keys, annotations.values)}
    elif isinstance(annotations, ast.Call):
        hints = {keyword.arg: _constant(keyword.value) for keyword in annotations.keywords}
    return name if isinstance(name, str) else None, description if isinstance(description, str) else None, hints


def _tool_from_node(node: ast.FunctionDef | ast.AsyncFunctionDef, decorator: ast.expr, relative_file: str,
                    helpers: dict[str, ast.AST], module_secrets: set[str],
                    dependencies: tuple[list[str], list[str], list[str]]) -> Tool:
    name, description, hints = _hints(decorator)
    summary, argument_docs = _docstring(node)
    properties: dict[str, Any] = {}
    required: list[str] = []
    supported = True
    positional = list(node.args.posonlyargs) + list(node.args.args)
    defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
    arguments = list(zip(positional, defaults)) + list(zip(node.args.kwonlyargs, node.args.kw_defaults))
    for argument, default in arguments:
        if _is_context(argument.annotation):
            continue
        schema, argument_supported, is_required = _parameter(argument, default)
        if argument.arg in argument_docs and "description" not in schema:
            schema["description"] = argument_docs[argument.arg]
        supported = supported and argument_supported
        properties[argument.arg] = schema
        if is_required:
            required.append(argument.arg)

    effects = _effects(node, helpers)
    secrets = sorted(effects.secrets | module_secrets)
    pinned, unresolved, frameworks = dependencies
    mutations = sorted(effects.mutations)
    reasons: list[str] = []
    warnings: list[str] = []
    if hints.get("readOnlyHint") is False or hints.get("destructiveHint") is True:
        reasons.append("declared as mutating by its MCP tool annotations; use safe outputs instead")
    if mutations:
        prefix = "declares readOnlyHint but " if hints.get("readOnlyHint") is True else ""
        reasons += [f"{prefix}{mutation}; mcp-scripts must be read-only, use safe outputs instead" for mutation in mutations]
    reasons += sorted(effects.processes)
    if not supported:
        reasons.append("one or more annotations are outside the mcp-scripts input schema subset")
    if node.args.vararg or node.args.kwarg:
        reasons.append("*args/**kwargs cannot be described as mcp-scripts inputs")
    blocking = bool(reasons)
    if effects.shared_state:
        reasons.append("mutable shared state is not preserved between invocations")
    reasons += sorted(effects.file_reads)
    if unresolved:
        reasons.append(f"cannot pin dependencies {', '.join(unresolved)}: install the server's environment before compiling")
    classification = "incompatible" if blocking else "partially portable" if reasons else "portable"

    if secrets:
        warnings.append(f"map {', '.join(secrets)} as repository or environment secrets")
    if isinstance(node, ast.AsyncFunctionDef):
        warnings.append("async function is executed with asyncio.run() on each call")
    if "readOnlyHint" not in hints and not mutations:
        warnings.append("no readOnlyHint annotation: read-only status is inferred by static analysis only")
    returns, _ = _annotation(node.returns) if node.returns is not None else ({"type": None}, True)
    if returns.get("type") in {"array", "object"}:
        warnings.append("results over 500 characters are delivered to the agent as a file path")

    module_parts = Path(relative_file).with_suffix("").parts
    return Tool(
        name=name or node.name,
        function=node.name,
        source_file=relative_file,
        module=".".join(module_parts[:-1] if module_parts[-1] == "__init__" else module_parts),
        line=node.lineno,
        description=description or summary or "No description provided.",
        schema={"type": "object", "properties": properties, "required": required},
        returns=returns.get("type"),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        read_only=not mutations and hints.get("readOnlyHint") is not False and hints.get("destructiveHint") is not True,
        mutations=mutations,
        secrets=secrets,
        dependencies=pinned,
        framework_imports=frameworks,
        classification=classification,
        reasons=reasons or ["JSON-compatible read-only function"],
        warnings=warnings,
    )


def _python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts[:-1]
        if any(part.startswith(".") or part in SKIPPED_DIRECTORIES for part in parts):
            continue
        yield path


def discover(repository: str | Path) -> list[Tool]:
    root = Path(repository).resolve()
    tools: list[Tool] = []
    for path in _python_files(root):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        servers = _servers(tree)
        helpers = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        module_secrets = _module_secrets(tree)
        dependencies = None
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorator = _tool_decorator(node, servers)
            if decorator is None:
                continue
            dependencies = dependencies or _dependencies(tree, root, path.parent)
            tools.append(_tool_from_node(node, decorator, path.relative_to(root).as_posix(),
                                         helpers, module_secrets, dependencies))
    return tools


def repository_root(source_root: Path) -> Path:
    """Return the enclosing git checkout, which is the working directory on the Actions runner."""
    for candidate in (source_root, *source_root.parents):
        if (candidate / ".git").exists():
            return candidate
    return source_root


def manifest(repository: str | Path) -> dict[str, Any]:
    source_root = Path(repository).resolve()
    checkout = repository_root(source_root)
    return {
        "compiler_version": __version__,
        "repository": checkout.name,
        "source_path": source_root.relative_to(checkout).as_posix() or ".",
        "tools": [tool.to_dict() for tool in discover(source_root)],
    }


def manifest_json(repository: str | Path) -> str:
    return json.dumps(manifest(repository), indent=2)
