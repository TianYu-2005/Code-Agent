# Code Agent

A modular terminal coding agent implemented without an agent framework.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-packages
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

See [`docs/design.md`](docs/design.md) for the architecture and
[`docs/development-guide.md`](docs/development-guide.md) for the development workflow.
