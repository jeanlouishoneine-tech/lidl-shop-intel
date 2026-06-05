FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

COPY src/ ./src/
COPY .env.example ./.env.example

EXPOSE 8050

CMD ["uv", "run", "python", "src/app.py"]
