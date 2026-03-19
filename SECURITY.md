# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability, **do not open a public issue.** Email the maintainer directly or use GitHub's private vulnerability reporting.

## Scope

This is a self-hosted application. The operator is responsible for:
- Keeping API keys secure (never commit `.env`)
- Restricting network access (firewall, reverse proxy)
- Keeping Docker images updated
- Setting strong values for `STOURIO_API_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`

## Built-in Security

- **CyberSecurity agent** monitors all agent actions for threats
- **Inline interceptor** blocks high-risk tool calls pending human approval
- **User restriction** via `TELEGRAM_ALLOWED_USER_IDS`
- **API authentication** via `X-STOURIO-KEY` header on all endpoints
- **File sandbox** restricts file operations to `WORKSPACE_DIR`
- **Code execution timeout** prevents runaway processes
