# Security Policy

## Scope

MCP Compile generates code and GitHub Actions workflow artifacts. Generated
artifacts run with the permissions, network access, and secrets provided by the
caller. Review them before execution.

Do not provide production secrets to workflows running code from untrusted pull
requests. Do not use generated MCP Scripts for mutating operations without an
independent security review and an appropriate approval boundary.

## Reporting a vulnerability

Please do not open a public issue for a suspected security vulnerability. Use
GitHub's private vulnerability reporting feature when it is enabled for this
repository. If it is unavailable, contact the repository maintainers privately
with reproduction steps, impact, and a suggested mitigation.

Allow reasonable time for investigation and remediation before public
disclosure. Please avoid including secrets or personal data in reports.
