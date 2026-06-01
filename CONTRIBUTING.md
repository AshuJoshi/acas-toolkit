# Contributing

Thanks for your interest in ACAS Toolkit.

## Filing an issue

Please include:
* Python version (`python --version`)
* `acas-toolkit` version (`pip show acas-toolkit`)
* Azure region you're hitting
* Minimal reproducer

## Filing a PR

1. Fork and create a branch off `main`
2. `uv sync --extra dev`
3. `uv run ruff check . && uv run ruff format --check .`
4. `uv run pytest tests/`
5. Open a PR describing the change

## Local development

```bash
uv sync --extra dev
source .venv/bin/activate
```

## Code of conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).
