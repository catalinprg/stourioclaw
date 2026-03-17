from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select

from src.persistence.database import DocumentChunk, async_session
from src.rag.embeddings.base import BaseEmbedder
from src.rag.reranker.base import BaseReranker

logger = logging.getLogger("stourio.rag.retriever")


@dataclass
class RetrievalResult:
    content: str
    score: float
    metadata: dict
    source_path: str
    section_header: str


class Retriever:
    def __init__(self, embedder: BaseEmbedder, reranker: Optional[BaseReranker] = None):
        self.embedder = embedder
        self.reranker = reranker

    async def search(
        self,
        query: str,
        source_type: Optional[str] = None,
        metadata_filter: Optional[dict] = None,
        top_k_vector: int = 20,
        top_k_final: int = 3,
    ) -> list[RetrievalResult]:
        # Embed the query
        query_embeddings = await self.embedder.embed([query])
        query_embedding = query_embeddings[0]

        async with async_session() as session:
            distance_col = DocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
            stmt = select(DocumentChunk, distance_col)

            if source_type:
                stmt = stmt.where(DocumentChunk.source_type == source_type)

            if metadata_filter:
                for key, value in metadata_filter.items():
                    stmt = stmt.where(DocumentChunk.metadata_[key].astext == str(value))

            stmt = stmt.order_by(distance_col).limit(top_k_vector)
            result = await session.execute(stmt)
            rows = result.all()

        if not rows:
            return []

        if self.reranker:
            documents = [row[0].content for row in rows]
            ranked = await self.reranker.rerank(query=query, documents=documents, top_k=top_k_final)
            results = []
            for rd in ranked:
                chunk = rows[rd.index][0]
                results.append(
                    RetrievalResult(
                        content=rd.content,
                        score=rd.score,
                        metadata=chunk.metadata_ or {},
                        source_path=chunk.source_path or "",
                        section_header=chunk.section_header or "",
                    )
                )
            return results
        else:
            results = []
            for chunk, distance in rows[:top_k_final]:
                results.append(
                    RetrievalResult(
                        content=chunk.content,
                        score=1.0 - distance,
                        metadata=chunk.metadata_ or {},
                        source_path=chunk.source_path or "",
                        section_header=chunk.section_header or "",
                    )
                )
            return results
