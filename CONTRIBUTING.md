# Contributing to Stourio Engine

Contributions are welcome. Here's how to get involved.

## Getting Started

1. Fork the repository
2. Clone your fork and create a feature branch:
   ```bash
   git checkout -b feat/your-feature
   ```
3. Follow the [Quick Start](README.md#quick-start) to set up a local environment
4. Make your changes, add tests, and verify everything works

## Submitting Changes

1. **Open an issue first** — describe what you want to change and why. This avoids wasted effort on changes that don't align with the project direction.
2. Keep pull requests focused. One logical change per PR.
3. Write clear commit messages in imperative mood (`add X`, `fix Y`, not `added X` or `fixes Y`).
4. Ensure all existing tests pass: `pytest stourio-core-engine/tests/`
5. Add tests for new functionality.

## Code Style

- Python 3.12+
- Follow existing patterns in the codebase
- Type hints on public interfaces
- No hardcoded secrets — use environment variables

## What to Contribute

Good first contributions:
- Bug fixes with a failing test
- New tool plugins (YAML or Python)
- New notification adapters
- Documentation improvements
- Additional agent templates

Larger efforts (open an issue to discuss first):
- New LLM provider adapters
- Changes to the orchestrator routing logic
- New chain execution strategies

## Reporting Bugs

Open a [GitHub issue](https://github.com/catalinprg/ai-ops-engine/issues) with:
- Steps to reproduce
- Expected vs actual behavior
- Docker logs if applicable

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
