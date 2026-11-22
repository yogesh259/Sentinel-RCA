FROM python:3.11-slim

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install production dependencies and spaCy language model
RUN uv sync --frozen --no-dev && uv run python -m spacy download en_core_web_sm

# Copy application code and console
COPY src/ src/
COPY ui/ ui/
COPY evals/ evals/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
