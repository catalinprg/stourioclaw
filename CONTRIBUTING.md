# Contributing

Contributions are welcome. Here's how to get started.

## Setup

```bash
git clone https://github.com/catalinprg/stourioclaw.git
cd stourioclaw
cp .env.example .env
# Fill in your API keys
docker compose up -d
```

## Development

- Python 3.12, FastAPI, SQLAlchemy async, PostgreSQL + pgvector, Redis
- Tests: `pytest tests/ -v`
- Code style: keep it simple, no over-engineering

## Pull Requests

1. Fork the repo
2. Create a feature branch
3. Write tests for new functionality
4. Ensure all tests pass
5. Submit a PR with a clear description of what and why

## Reporting Issues

Open an issue with:
- What you expected
- What happened
- Steps to reproduce
- Relevant logs (`docker compose logs stourioclaw --tail=50`)
