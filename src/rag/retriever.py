from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, func

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
        # Embed the query for vector search
        query_embeddings = await self.embedder.embed([query])
        query_embedding = query_embeddings[0]

        async with async_session() as session:
            # --- Vector search ---
            distance_col = DocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
            vector_stmt = select(DocumentChunk.id, distance_col)

            if source_type:
                vector_stmt = vector_stmt.where(DocumentChunk.source_type == source_type)
            if metadata_filter:
                for key, value in metadata_filter.items():
                    vector_stmt = vector_stmt.where(DocumentChunk.metadata_[key].astext == str(value))

            vector_stmt = vector_stmt.order_by(distance_col).limit(top_k_vector)
            vector_result = await session.execute(vector_stmt)
            vector_rows = vector_result.all()

            # --- BM25 full-text search ---
            ts_query = func.plainto_tsquery('english', query)
            ts_rank = func.ts_rank(DocumentChunk.tsv, ts_query).label("bm25_rank")
            bm25_stmt = (
                select(DocumentChunk.id, ts_rank)
                .where(DocumentChunk.tsv.op('@@')(ts_query))
            )

            if source_type:
                bm25_stmt = bm25_stmt.where(DocumentChunk.source_type == source_type)
            if metadata_filter:
                for key, value in metadata_filter.items():
                    bm25_stmt = bm25_stmt.where(DocumentChunk.metadata_[key].astext == str(value))

            bm25_stmt = bm25_stmt.order_by(ts_rank.desc()).limit(top_k_vector)
            bm25_result = await session.execute(bm25_stmt)
            bm25_rows = bm25_result.all()

            # --- Reciprocal Rank Fusion ---
            k = 60  # RRF constant
            scores: dict[str, float] = {}

            for rank, (chunk_id, _) in enumerate(vector_rows):
                scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank + 1)

            for rank, (chunk_id, _) in enumerate(bm25_rows):
                scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (k + rank + 1)

            if not scores:
                return []

            # Fetch top chunks by fused score
            top_ids = sorted(scores, key=scores.get, reverse=True)[:top_k_vector]
            chunks_result = await session.execute(
                select(DocumentChunk).where(DocumentChunk.id.in_(top_ids))
            )
            chunks_by_id = {c.id: c for c in chunks_result.scalars().all()}

            # Order by fused score
            ranked_chunks = [(chunks_by_id[cid], scores[cid]) for cid in top_ids if cid in chunks_by_id]

        # Optional reranker
        if self.reranker and ranked_chunks:
            documents = [chunk.content for chunk, _ in ranked_chunks]
            ranked = await self.reranker.rerank(query=query, documents=documents, top_k=top_k_final)
            results = []
            for rd in ranked:
                chunk = ranked_chunks[rd.index][0]
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
            return [
                RetrievalResult(
                    content=chunk.content,
                    score=score,
                    metadata=chunk.metadata_ or {},
                    source_path=chunk.source_path or "",
                    section_header=chunk.section_header or "",
                )
                for chunk, score in ranked_chunks[:top_k_final]
            ]
