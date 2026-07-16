FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.6
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./frontend/dist
COPY evals ./evals
COPY docs ./docs
COPY infra/sql ./infra/sql
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8080 8081
CMD ["control-api"]
