# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest on `main` | Yes |

## Reporting a Vulnerability

If you discover a security vulnerability in Stourio Engine, please report it responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities.
2. Open a [GitHub issue](https://github.com/catalinprg/ai-ops-engine/issues) with the `security` label, or email the maintainers directly if the vulnerability is sensitive.
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will acknowledge receipt within 48 hours and aim to provide an initial assessment within 7 days.

## Security Architecture

- All API endpoints require `X-STOURIO-KEY` authentication
- The MCP Gateway uses a separate `MCP_SHARED_SECRET` bearer token
- Database credentials and API keys are injected via environment variables — never committed to source
- The kill switch provides immediate halt of all autonomous operations
- Human-in-the-loop approval gates prevent high-risk actions from executing without review
- All decisions are logged to an immutable audit trail

## Best Practices for Operators

- Rotate `X-STOURIO-KEY` and `MCP_SHARED_SECRET` regularly
- Use the `.env.example` files as templates — never commit `.env` files
- Run services behind a reverse proxy with TLS in production
- Restrict Docker port bindings to `127.0.0.1` (default in docker-compose.yml)
- Review the audit trail (`GET /api/audit`) for unexpected agent behavior
- Enable the kill switch (`POST /api/kill`) immediately if you observe anomalous activity
