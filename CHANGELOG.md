# Changelog

## v1.0.0 - (2026-09-24)

**Fixed bug:**
- **Fixed the Python block:** the wrong indentation is gone. The YAML is now built by a small writer instead of string templates, so that kind of indentation bug can't come back.
- **No FastMCP on the runner:** the generated code replaces FastMCP with a small stand-in, so tools no longer fail on `import fastmcp`. Other third-party imports are pinned to the versions installed where you run the compiler and listed under `dependencies:`.
- **Contract tests check the real code:** they read the generated YAML, run each `py:` block with a predefined `inputs` dict as gh-aw does, and compare the result with the original function.
- **Importable shared file:** the tools now live in `.github/workflows/shared/<name>-tools.md`. The main workflow imports it and lists which tools were left out.
- **Smaller fixes:**
  - `env:` only appears when a tool needs secrets.
  - The source path is relative to the git root, so it no longer depends on the folder you compile from.
  - Printing inside a tool no longer corrupts its output.


**CLI and safety**:
- **New options:** `--engine`, `--on`, `--timeout` and `--tool-timeout TOOL=SECONDS`.
- **Output-folder guard:** the compiler refuses to write to a folder that contains the source, or to a non-empty folder it didn't create.

**Cost and latency numbers removed:** those fields are gone, and the report describes the tradeoffs in words.

**Tests and docs:** the sample server has a new async `text_stats` tool, the tests went from 4 to 9, `pyyaml` was added to the test dependencies, and the README is updated.

