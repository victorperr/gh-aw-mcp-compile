import os
from typing import Annotated, Literal

from fastmcp import FastMCP


mcp = FastMCP("mcp-compile-demo")


@mcp.tool
def greet(name: str) -> str:
    """Return a greeting without external state."""
    return f"Hello, {name}!"


@mcp.tool
def read_account(account_id: str) -> dict:
    """Read an account using an explicitly mapped API credential.

    Args:
        account_id: Identifier of the account to read.
    """
    _ = os.getenv("ACCOUNTS_API_KEY")
    return {"account_id": account_id, "status": "demo"}


@mcp.tool(annotations={"readOnlyHint": True})
async def text_stats(
    text: Annotated[str, "Text to analyze"],
    unit: Literal["words", "characters"] = "words",
) -> dict:
    """Count words or characters in a text."""
    count = len(text.split()) if unit == "words" else len(text)
    return {"unit": unit, "count": count}


@mcp.tool
def save_report(path: str, content: str) -> str:
    """Write a report to disk; mutating tools belong in safe outputs."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


if __name__ == "__main__":
    mcp.run()
