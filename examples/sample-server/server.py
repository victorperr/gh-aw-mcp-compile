import os


class _MCP:
    def tool(self, function):
        return function


mcp = _MCP()


@mcp.tool
def greet(name: str) -> str:
    """Return a greeting without external state."""
    return f"Hello, {name}!"


@mcp.tool
def read_account(account_id: str) -> dict:
    """Read an account using an explicitly mapped API credential."""
    _ = os.getenv("ACCOUNTS_API_KEY")
    return {"account_id": account_id, "status": "demo"}


@mcp.tool
def save_report(path: str, content: str) -> str:
    """Write a report to disk; this needs a stronger sandbox policy."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path
