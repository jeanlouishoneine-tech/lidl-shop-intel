# Contributing

## Development setup

```bash
uv sync --group dev
uv run pre-commit install
```

## Running tests

```bash
uv run pytest tests/ -v
```

## Lint and format

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## Type checking

```bash
uv run mypy src/
```

Pre-commit hooks run ruff and mypy automatically on every commit once installed.

## Notes

- `.env` must never be committed — it contains your Lidl refresh token.
- All tests use an isolated temporary database; the production `data/lidl.db` is never touched.
