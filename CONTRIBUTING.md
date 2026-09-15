# Contributing

Thanks for helping improve MCP Compile. Small, focused pull requests are
welcome.

## Development setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
python -m pytest -q
```

Please add or update tests for behavioral changes. Keep generated artifacts out
of commits unless they are intentionally part of an example or fixture.

## Pull requests

Explain the user-visible behavior, security implications, and test coverage in
the pull request description. Do not include credentials, private repository
content, or generated files containing secrets.
