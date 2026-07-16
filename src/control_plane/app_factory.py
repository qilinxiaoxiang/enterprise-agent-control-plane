from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

from control_plane.api import router
from control_plane.config import Settings, get_settings
from control_plane.migrations import run_migrations
from control_plane.model_provider import recommendation_provider
from control_plane.repository import MemoryRepository, PostgresRepository, Repository
from control_plane.retrieval import PolicyRetriever, build_retriever
from control_plane.seed import seed_public_runs
from control_plane.service import ControlPlaneService
from control_plane.telemetry import configure_telemetry, flush_telemetry
from control_plane.tool_gateway import McpToolGateway, SyntheticToolGateway, ToolGateway
from control_plane.workflow import ControlledWorkflow


def build_repository(settings: Settings) -> Repository:
    if settings.repository_backend == "postgres":
        return PostgresRepository(settings.database_url)
    return MemoryRepository()


def build_tools(settings: Settings) -> ToolGateway:
    if settings.repository_backend == "postgres":
        audience = (
            settings.mcp_server_url.removesuffix("/mcp")
            if settings.app_env == "production"
            else None
        )
        return McpToolGateway(settings.mcp_server_url, audience=audience)
    return SyntheticToolGateway()


def create_app(
    settings: Settings | None = None,
    repository: Repository | None = None,
    tools: ToolGateway | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    repository = repository or build_repository(settings)
    tools = tools or build_tools(settings)
    retriever = build_retriever(settings)
    configure_telemetry(settings)

    def wire_service(
        checkpointer: BaseCheckpointSaver[Any], policy_retriever: PolicyRetriever
    ) -> None:
        workflow = ControlledWorkflow(
            repository,
            tools,
            recommendation_provider(settings),
            checkpointer=checkpointer,
            retriever=policy_retriever,
        )
        app.state.service = ControlPlaneService(repository, workflow)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.repository_backend == "postgres":
            run_migrations(settings.database_url)
        seed_public_runs(repository)
        await retriever.seed()
        if settings.repository_backend == "postgres":
            separator = "&" if "?" in settings.database_url else "?"
            checkpoint_url = (
                f"{settings.database_url}{separator}"
                "options=-csearch_path%3Dlanggraph_checkpoints%2Cpublic"
            )
            async with AsyncPostgresSaver.from_conn_string(checkpoint_url) as checkpointer:
                await checkpointer.setup()
                wire_service(checkpointer, retriever)
                yield
        else:
            wire_service(InMemorySaver(), retriever)
            yield

    app = FastAPI(
        title="Enterprise Agent Reliability Control Plane",
        version="0.1.0",
        description="Synthetic regulated financial workflow with deterministic controls.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Signature"],
    )
    app.state.settings = settings
    app.state.repository = repository
    app.state.tools = tools

    @app.middleware("http")
    async def flush_cloud_trace(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        if settings.app_env == "production":
            flush_telemetry()
        return response

    app.include_router(router)
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()

    frontend = Path(settings.frontend_dir)
    if frontend.is_dir():
        assets = frontend / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> Any:
            candidate = frontend / path
            if path and candidate.is_file() and frontend.resolve() in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(frontend / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, str]:
            return {
                "service": "enterprise-agent-control-plane",
                "console": "run `cd frontend && npm run dev` during source development",
                "docs": "/docs",
            }

    return app
