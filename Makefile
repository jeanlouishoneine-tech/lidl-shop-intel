.PHONY: run test lint format auth docker-build

run:
	uv run python src/app.py

test:
	uv run pytest tests/ -v --tb=short

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

auth:
	uv run python src/auth.py

docker-build:
	docker build -t lidl-app .
