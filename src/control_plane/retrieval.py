from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime
from typing import Protocol

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_postgres import PGVector

from control_plane.config import Settings
from control_plane.domain import CaseRequest, PolicyCitation
from control_plane.policy import POLICIES, retrieve_policies


class PolicyRetriever(Protocol):
    async def seed(self) -> None: ...
    async def retrieve(self, request: CaseRequest, limit: int = 5) -> list[PolicyCitation]: ...


class DeterministicPolicyRetriever:
    async def seed(self) -> None:
        return None

    async def retrieve(self, request: CaseRequest, limit: int = 5) -> list[PolicyCitation]:
        return retrieve_policies(request, limit)


class HashEmbeddings(Embeddings):
    """Stable 768-dimension local embedding for offline pgvector integration tests."""

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[a-z0-9_]+", text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[index] += 1 if digest[4] & 1 else -1
        norm = math.sqrt(sum(value * value for value in vector)) or 1
        return [value / norm for value in vector]


class PgVectorPolicyRetriever:
    def __init__(self, settings: Settings) -> None:
        connection = settings.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        embeddings: Embeddings
        if settings.model_provider == "vertex":
            if not settings.google_cloud_project:
                raise ValueError("GOOGLE_CLOUD_PROJECT is required for Vertex embeddings")
            from langchain_google_genai import GoogleGenerativeAIEmbeddings

            embeddings = GoogleGenerativeAIEmbeddings(
                model=settings.embedding_model,
                project=settings.google_cloud_project,
                location=settings.google_cloud_location,
                vertexai=True,
                output_dimensionality=settings.embedding_dimensions,
            )
        else:
            embeddings = HashEmbeddings(settings.embedding_dimensions)
        self.store = PGVector(
            embeddings=embeddings,
            connection=connection,
            collection_name="synthetic_financial_policies",
            embedding_length=settings.embedding_dimensions,
            use_jsonb=True,
            create_extension=False,
            async_mode=True,
        )

    async def seed(self) -> None:
        existing = await self.store.asimilarity_search("transfer policy", k=1)
        if existing:
            return
        documents = [
            Document(
                page_content=(f"{policy.policy_id} {policy.section}. {policy.text}"),
                metadata={
                    "policy_id": policy.policy_id,
                    "version": policy.version,
                    "section": policy.section,
                    "effective_at": policy.effective_at.isoformat(),
                },
            )
            for policy in POLICIES
        ]
        await self.store.aadd_documents(documents)

    async def retrieve(self, request: CaseRequest, limit: int = 5) -> list[PolicyCitation]:
        query = (
            f"{request.requested_action} {request.anomaly_type} KYC AML tenant authorization "
            "idempotent effect verification"
        )
        matches = await self.store.asimilarity_search_with_relevance_scores(query, k=limit)
        return [
            PolicyCitation(
                policy_id=str(document.metadata["policy_id"]),
                version=str(document.metadata["version"]),
                section=str(document.metadata["section"]),
                text=document.page_content.split(". ", 1)[-1],
                score=max(0.0, min(1.0, float(score))),
                effective_at=datetime.fromisoformat(str(document.metadata["effective_at"])),
            )
            for document, score in matches
        ]


def build_retriever(settings: Settings) -> PolicyRetriever:
    if settings.repository_backend == "postgres":
        return PgVectorPolicyRetriever(settings)
    return DeterministicPolicyRetriever()
