from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "test", "production"] = "local"
    auth_mode: Literal["dev", "firebase"] = "dev"
    model_provider: Literal["deterministic", "vertex"] = "deterministic"
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    model_name: str = "gemini-2.5-flash"
    # The model receives an already-computed deterministic decision and performs
    # bounded schema-constrained classification, not open-ended reasoning.
    vertex_thinking_budget: int = Field(default=0, ge=0)
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    database_url: str = "postgresql://control:control@localhost:5432/control"
    repository_backend: Literal["memory", "postgres"] = "memory"
    mcp_server_url: str = "http://localhost:8081/mcp"
    webhook_secret: str = "local-synthetic-secret"
    public_demo_only: bool = True
    otel_exporter_otlp_endpoint: str | None = None
    port: int = Field(default=8080, ge=1, le=65535)
    frontend_dir: Path = Path("frontend/dist")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
