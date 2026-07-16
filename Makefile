.PHONY: setup check test integration eval demo down frontend-build

setup:
	uv sync --extra dev
	cd frontend && npm ci

test:
	uv run pytest --cov=control_plane --cov=tool_server --cov-report=term-missing

integration:
	INTEGRATION_DATABASE_URL=postgresql://control:control@localhost:5432/control \
	INTEGRATION_MCP_URL=http://localhost:8081/mcp uv run pytest -m integration

frontend-build:
	cd frontend && npm run build

check:
	uv run ruff check src tests
	uv run mypy src
	uv run pytest
	terraform -chdir=infra/terraform validate
	cd frontend && npm run lint && npm run build

eval:
	uv run control-eval --suite evals/cases.jsonl --output evals/results/latest.json

demo:
	docker compose up --build

down:
	docker compose down --remove-orphans
